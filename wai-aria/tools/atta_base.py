#!/usr/bin/env python3
#
# atta_base
# Optional base class for python3 Accessible Technology Test Adapters
#
# Developed by Joanmarie Diggs (@joanmarie)
# Copyright (c) 2016-2019 Igalia, S.L.
#
# For license information, see:
# https://www.w3.org/Consortium/Legal/2008/04-testsuite-copyright.html

import argparse
import faulthandler
import html
import json
import re
import signal
import sys
import threading
import time
import traceback

from http.server import HTTPServer
from urllib import parse, request
from atta_assertion import AttaAssertion, AttaEventAssertion
from atta_request_handler import AttaRequestHandler


class Atta:
    """Optional base class for python3 Accessible Technology Test Adapters."""

    STATUS_ERROR = "ERROR"
    STATUS_OK = "OK"

    FAILURE_ATTA_NOT_ENABLED = "ATTA not enabled"
    FAILURE_ATTA_NOT_READY = "ATTA not ready"
    FAILURE_ELEMENT_NOT_FOUND = "Element not found"

    LOG_DEBUG = 0
    LOG_INFO = 1
    LOG_WARNING = 2
    LOG_ERROR = 3
    LOG_NONE = 100

    LOG_LEVELS = {
        LOG_DEBUG: "DEBUG",
        LOG_INFO: "INFO",
        LOG_WARNING: "WARNING",
        LOG_ERROR: "ERROR",
        LOG_NONE: "NONE",
    }

    FORMAT_NONE = "%(label)s%(msg)s"
    FORMAT_NORMAL = "\x1b[1m%(label)s\x1b[22m%(msg)s\x1b[0m"
    FORMAT_GOOD = "\x1b[32;1m%(label)s\x1b[22m%(msg)s\x1b[0m"
    FORMAT_WARNING = "\x1b[33;1m%(label)s\x1b[22m%(msg)s\x1b[0m"
    FORMAT_BAD = "\x1b[31;1m%(label)s\x1b[22m%(msg)s\x1b[0m"

    @classmethod
    def get_cmdline_options(cls):
        parser = argparse.ArgumentParser()

        _help = "(default: %(default)s)"

        parser.add_argument("--host", action="store", default="localhost",
                            help="host on which to run this ATTA %s" % _help)
        parser.add_argument("--port", action="store", type=int, default="4119",
                            help="port on which to run this ATTA %s" % _help)
        parser.add_argument("--log", metavar="LEVEL", action="store", default="info",
                            choices=["debug", "info", "warning", "error", "none"],
                            help="amount of log output desired %s" % _help)
        parser.add_argument("--use-ansi", action="store_true",
                            help="use ANSI-formatted log output %s" % _help)
        parser.add_argument("--no-load-events", action="store_true",
                            help="user agent fails to emit load events %s" % _help)
        parser.add_argument("--any-name-source", action="store_true",
                            help="accept any accessible name source %s" % _help)

        parsed = vars(parser.parse_args())
        log = parsed.get("log").upper()
        parsed["log"] = [k for k, v in Atta.LOG_LEVELS.items() if log == v][0]
        return parsed

    def __init__(self, name, version, api):
        """Initializes this ATTA."""

        options = Atta.get_cmdline_options()

        self._host = options.get("host")
        self._port = options.get("port")
        self._log_level = options.get("log")
        self._ansi_formatting = options.get("use_ansi")
        self._load_notifications = not options.get("no_load_events")
        self._any_name_source = options.get("any_name_source")
        self._atta_name = name
        self._atta_version = version
        self._api_name = api
        self._api_version = ""
        self._server = None
        self._server_thread = None
        self._enabled = False
        self._ready = False
        self._next_test = None, ""
        self._current_document = None
        self._current_window = None
        self._current_application = None
        self._results = {}
        self._monitored_event_types = []
        self._event_history = []
        self._listeners = {}
        self._supported_methods = {}
        self._supported_relation_types = []
        self._bugs = {}
        self._event_test_delay = 1.0

        if not sys.version_info[0] == 3:
            self._print(self.LOG_ERROR, "This ATTA requires Python 3.")
            return

        self._api_version = self._get_system_api_version()

        if not self._get_accessibility_enabled() \
           and not self._set_accessibility_enabled(True):
            return

        self._supported_methods = self.get_supported_methods()
        self._supported_relation_types = self.get_supported_relation_types()
        self._enabled = True

    @staticmethod
    def _on_exception():
        """Handles exceptions, returning a string with the error."""

        return "\nEXCEPTION: %s" % traceback.format_exc(limit=1, chain=False)

    def log_message(self, string, level=None):
        """Logs string, typically printing it to stdout."""

        if level is None:
            level = self._log_level

        self._print(level, string)

    def _print(self, level, string, label=None, formatting=None, **kwargs):
        """Writes string to stdout if level is >= this ATTA's debug level."""

        if level < self._log_level:
            return

        if label is None:
            label = "%s: " % self.LOG_LEVELS.get(level)

        if level in [self.LOG_WARNING, self.LOG_ERROR]:
            info = parse.urlsplit(self._next_test[1])
            if info.path and not (info.path in string or info.path in label):
                tokens = list(filter(lambda x: x.strip(), label.rsplit(":", 1)))
                tokens.append("(%s): " % info.path)
                label = " ".join(tokens)

        if formatting is None:
            if not self._ansi_formatting:
                formatting = self.FORMAT_NONE
            elif level == self.LOG_ERROR:
                formatting = self.FORMAT_BAD
            elif level == self.LOG_WARNING:
                formatting = self.FORMAT_WARNING
            else:
                formatting = self.FORMAT_NORMAL

        sys.stdout.write("%s\n" % formatting % {"label": label, "msg": string})

    def start(self, **kwargs):
        """Starts this ATTA (i.e. before running a series of tests)."""

        if not self._enabled:
            self._print(self.LOG_ERROR, "Start failed because ATTA is not enabled.")
            return

        faulthandler.enable(all_threads=False)
        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)

        self._print(self.LOG_INFO, "Starting on http://%s:%s/" % (self._host, self._port), "SERVER: ")
        self._server = HTTPServer((self._host, self._port), AttaRequestHandler)
        AttaRequestHandler.set_atta(self)

        if self._server_thread is None:
            self._server_thread = threading.Thread(target=self._server.serve_forever)
            self._server_thread.start()

    def get_info(self, **kwargs):
        """Returns a dict of details about this ATTA needed by the harness."""

        return {"ATTAname": self._atta_name,
                "ATTAversion": self._atta_version,
                "API": self._api_name,
                "APIversion": self._api_version}

    def is_enabled(self, **kwargs):
        """Returns True if this ATTA is enabled."""

        return self._enabled

    def is_ready(self, document=None, **kwargs):
        """Returns True if this ATTA is able to proceed with a test run."""

        if self._ready:
            return True

        document = document or self._current_document
        if document is None and not self._load_notifications:
            self._current_window = self._find_test_window()
            document = self._find_descendant(self._current_window, self._is_document)

        if document is None:
            return False

        test_name, test_uri = self._next_test
        uri = self._get_uri(document)
        msg = "'%s' (%s)." % (test_name, parse.urlsplit(test_uri).path)

        self._ready = uri and uri == test_uri
        if self._ready:
            self._current_document = document
            self._print(self.LOG_DEBUG, msg, "READY: ")
            return True

        if not uri and test_name:
            name = self._get_title(document)
            self._ready = name == test_name

        if self._ready:
            self._current_document = document
            self._print(self.LOG_WARNING, "Matched '%s', but no URI" % name, "READY: ")
            return True

        msg = "%s Document URI: '%s'" % (msg, parse.urlsplit(uri).path)
        self._print(self.LOG_DEBUG, msg, "NOT READY: ")
        return False

    def start_test_run(self, name, url, **kwargs):
        """Sets the test details the ATTA should be looking for. The ATTA should
        update its "ready" status upon finding that file."""

        self._print(self.LOG_INFO, "%s (%s)" % (name, url), "\nSTART TEST RUN: ")
        if self._next_test == (name, url):
            return

        self._ready = False
        self._next_test = name, url

    def start_listen(self, event_types, **kwargs):
        """Causes the ATTA to start listening for the specified events."""

        self._print(self.LOG_DEBUG, "%s" % event_types, "START LISTEN: ")
        self._monitored_event_types = []
        self._event_history = []

        for event_type in event_types:
            self._register_listener(event_type, self._on_test_event, **kwargs)
            self._monitored_event_types.append(event_type)

    def _run_test(self, obj, assertion, **kwargs):
        """Runs a single assertion on obj, returning a results dict."""

        bug = ""
        test_class = self._get_assertion_test_class(assertion)
        if test_class is None:
            result = AttaAssertion.STATUS_FAIL
            message = "ERROR: %s is not a valid assertion" % assertion
            log = message
        else:
            if test_class == AttaEventAssertion:
                time.sleep(self._event_test_delay)

            test = test_class(obj, assertion, self)
            result, message, log = test.run()
            if result == AttaAssertion.STATUS_FAIL:
                bug = test.get_bug()

        test_file = parse.urlsplit(self._next_test[1]).path
        status_results = self._results.get(bug or result, {})
        file_results = status_results.get(test_file, [])
        file_results.append(" ".join(map(str, assertion)))
        status_results[test_file] = file_results
        self._results[bug or result] = status_results

        if not self._ansi_formatting:
            formatting = self.FORMAT_NONE
        elif result == AttaAssertion.STATUS_PASS:
            formatting = self.FORMAT_GOOD
        elif not test_class:
            formatting = self.FORMAT_BAD
        elif result == AttaAssertion.STATUS_FAIL:
            if bug:
                formatting = self.FORMAT_WARNING
            else:
                formatting = self.FORMAT_BAD
        else:
            formatting = self.FORMAT_WARNING

        string = "%s %s %s '%s'" % (*assertion[0:3], assertion[3])
        if message:
            string = "%s %s" % (string, message)

        self._print(self.LOG_INFO, string, "%s: " % result, formatting)
        return {"result": result, "message": message, "log": log}

    def run_tests(self, obj_id, assertions):
        """Runs the assertions on the object with the specified id, returning
        a dict with the results, the status of the run, and any messages."""

        if not self.is_enabled():
            self._print(self.LOG_WARNING, "ATTA is not enabled", "RUN TESTS: ")
            return {"status": self.STATUS_ERROR,
                    "message": self.FAILURE_ATTA_NOT_ENABLED,
                    "results": []}

        if not self.is_ready():
            self._print(self.LOG_WARNING, "ATTA is not ready", "RUN TESTS: ")
            return {"status": self.STATUS_ERROR,
                    "message": self.FAILURE_ATTA_NOT_READY,
                    "results": []}

        to_run = self._create_platform_assertions(assertions)
        self._print(self.LOG_DEBUG, "%i assertion(s) for '%s' " % (len(to_run), obj_id), "RUN TESTS: ")

        obj = self._get_element_with_id(self._current_document, obj_id)
        if not obj:
            # We may be testing that an object is not exposed (e.g. because it is hidden).
            # But we may instead have a test-file error or an accessibility bug. So warn.
            self._print(self.LOG_WARNING, "Accessible element not found", "RUN TESTS: ")

        results = [self._run_test(obj, a) for a in to_run]
        return {"status": self.STATUS_OK, "results": results}

    def stop_listen(self, **kwargs):
        """Causes the ATTA to stop listening for the specified events."""

        self._print(self.LOG_DEBUG, "%s" % self._monitored_event_types, "STOP LISTEN: ")
        for event_type in self._monitored_event_types:
            self._deregister_listener(event_type, self._on_test_event, **kwargs)

        self._monitored_event_types = []
        self._event_history = []

    def end_test_run(self, **kwargs):
        """Cleans up cached information at the end of a test run."""

        name, url = self._next_test
        self._print(self.LOG_DEBUG, "%s (%s)" % (name, url), "STOP TEST RUN: ")

        self._current_document = None
        self._next_test = None, ""
        self._ready = False

    def log_results_summary(self, clear_results=True, **kwargs):
        """Logs a summary of test results."""

        results = self._results
        if clear_results:
            self._results = {}

        def output(key, label=None, detailed=False):
            try:
                result = results.pop(key)
            except:
                result = None
                n_files = 0
                n_assertions = 0
            else:
                n_files = len(result)
                n_assertions = sum(map(lambda x: len(set(x)), result.values()))

            string = "%i assertions from %i files" % (n_assertions, n_files)
            self._print(self.LOG_INFO, string, label or "%s: " % key)
            if not (result and detailed):
                return

            for i, (test, assertions) in enumerate(sorted(result.items())):
                msg = "%4i. %s (%i)" % (i + 1, test, len(set(assertions)))
                self._print(self.LOG_INFO, msg, "", self.FORMAT_NONE)

        output(AttaAssertion.STATUS_PASS)
        output(AttaAssertion.STATUS_FAIL, label="FAIL (unfiled): ", detailed=True)
        bugs = sorted([key for key in results.keys()])
        for bug in bugs:
            details = self.get_bug_details(bug)
            output(bug, label="FAIL (%s %s): " % (bug, details), detailed=False)

    def shutdown(self, signum=None, frame=None, **kwargs):
        """Shuts down this ATTA (i.e. after all tests have been run)."""

        if not self._enabled:
            return

        self._ready = False

        try:
            signal_string = "on signal %s" % signal.Signals(signum).name
        except AttributeError:
            signal_string = "on signal %s" % str(signum)
        except:
            signal_string = ""
        self._print(self.LOG_INFO, "Shutting down %s\n" % signal_string, "\nSERVER: ")
        self.log_results_summary()

        if self._server is not None:
            thread = threading.Thread(target=self._server.shutdown)
            thread.start()

    def _get_element_with_id(self, root, element_id, **kwargs):
        """Returns the accessible descendant of root with the specified id."""

        if not element_id:
            return None

        pred = lambda x: self._get_id(x) == element_id
        return self._find_descendant(root, pred, **kwargs)

    def _in_current_document(self, obj, **kwargs):
        """Returns True if obj is an element in the current test's document."""

        if not self._current_document:
            return False

        pred = lambda x: x == self._current_document
        if pred(obj):
            return True

        return self._find_ancestor(obj, pred, **kwargs) is not None

    def _find_ancestor(self, obj, pred, **kwargs):
        """Returns the ancestor of obj for which pred returns True."""

        if obj is None:
            return None

        parent = self._get_parent(obj)
        while parent:
            if pred(parent):
                return parent
            parent = self._get_parent(parent)

        return None

    def _find_descendant(self, root, pred, **kwargs):
        """Returns the descendant of root for which pred returns True."""

        if pred(root) or root is None:
            return root

        children = self._get_children(root, **kwargs)
        for child in children:
            descendant = self._find_descendant(child, pred, **kwargs)
            if descendant:
                return descendant

        return None

    def _get_title(self, obj, simplify_whitespace=False, **kwargs):
        """Returns the title of obj."""

        if obj is None:
            return ""

        for attribute in self._get_title_property_names(**kwargs):
            name = self.get_property_value(obj, attribute)
            if name:
                if simplify_whitespace:
                    name = " ".join(name.split())
                return name

        return ""

    def _find_test_window(self, **kwargs):
        """Searches for and returns window containing the current test document."""

        name = self._next_test[0]
        if not name:
            return None

        # We need to simplify whitespace because some user agents are doing so.
        name = " ".join(name.split())

        if self._current_window:
            if name in self._get_title(self._current_window, simplify_whitespace=True):
                return self._current_window
            self._current_window = None

        for app in self._get_running_applications():
            windows = self._get_children(app)
            for window in windows:
                if name in self._get_title(window, simplify_whitespace=True):
                    return window

        return None

    def _get_running_applications(self, **kwargs):
        """Returns a list of running accessible-application objects."""

        self._print(self.LOG_DEBUG, "_get_running_applications() not implemented")
        return []

    def _get_title_property_names(self, **kwargs):
        """Returns a list of property names to be used to obtain an object's title."""

        self._print(self.LOG_DEBUG, "_get_title_property_names() not implemented")
        return []

    def _is_document(self, obj, **kwargs):
        """Returns True if obj is a web document."""

        self._print(self.LOG_DEBUG, "_is_document() not implemented")
        return False

    def _get_rendering_engine(self, **kwargs):
        """Returns a string with details of the user agent's rendering engine."""

        self._print(self.LOG_DEBUG, "_get_rendering_engine() not implemented")
        return ""

    def _get_system_api_version(self, **kwargs):
        """Returns a string with the installed version of the accessibility API."""

        self._print(self.LOG_DEBUG, "_get_system_api_version() not implemented")
        return ""

    def _get_accessibility_enabled(self, **kwargs):
        """Returns True if accessibility support is enabled on this platform."""

        self._print(self.LOG_DEBUG, "_get_accessibility_enabled() not implemented")
        return False

    def _set_accessibility_enabled(self, enable, **kwargs):
        """Returns True if accessibility support was successfully set."""

        self._print(self.LOG_DEBUG, "_set_accessibility_enabled() not implemented")
        return False

    def _register_listener(self, event_type, callback, **kwargs):
        """Registers an accessible-event listener on the platform."""

        self._print(self.LOG_DEBUG, "_register_listener() not implemented")

    def _deregister_listener(self, event_type, callback, **kwargs):
        """De-registers an accessible-event listener on the platform."""

        self._print(self.LOG_DEBUG, "_deregister_listener() not implemented")

    def _get_assertion_test_class(self, assertion, **kwargs):
        """Returns the appropriate Assertion class for assertion."""

        return AttaAssertion.get_test_class(assertion)

    def _create_platform_assertions(self, assertions, **kwargs):
        """Performs platform-specific changes needed to harness assertions."""

        # The properties associated with accessible events are currently given to
        # us as individual subtests. Unlike other assertions, event properties are
        # not independent of one another. Because these should be tested as an all-
        # or-nothing assertion, we'll combine the subtest values into a dictionary
        # passed along with each subtest.

        is_new_event = lambda x: x and x[0] == "event" and x[1] == "type"
        indices = [i for i, x in enumerate(assertions) if is_new_event(x)]
        indices.append(len(assertions))

        platform_assertions = []
        properties = {}
        for i, (test_type, name, verb, value) in enumerate(assertions):
            if test_type != "event":
                platform_assertions.append([test_type, name, verb, value])
                continue

            properties[name] = value
            if i + 1 in indices:
                platform_assertions.append(["event", "event", "contains", properties])
                properties = {}

        return platform_assertions

    def _get_id(self, obj, **kwargs):
        """Returns the element id associated with obj or an empty string upon failure."""

        self._print(self.LOG_DEBUG, "_get_id() not implemented")
        return ""

    def _get_uri(self, document, **kwargs):
        """Returns the URI associated with document or an empty string upon failure."""

        self._print(self.LOG_DEBUG, "_get_uri() not implemented")
        return ""

    def _get_children(self, obj, **kwargs):
        """Returns the children of obj or [] upon failure or absence of children."""

        self._print(self.LOG_DEBUG, "_get_children() not implemented")
        return []

    def _get_parent(self, obj, **kwargs):
        """Returns the parent of obj or None upon failure."""

        self._print(self.LOG_DEBUG, "_get_parent() not implemented")
        return None

    def get_property_value(self, obj, property_name, **kwargs):
        """Returns the value of property_name for obj."""

        self._print(self.LOG_DEBUG, "get_property_value() not implemented")
        return None

    def get_relation_targets(self, obj, relation_type, **kwargs):
        """Returns the elements of pointed to by relation_type for obj."""

        self._print(self.LOG_DEBUG, "get_relation_targets() not implemented")
        return []

    def get_client_side_method(self, server_side_method, **kwargs):
        """Returns the client-side API method for server_side_method."""

        self._print(self.LOG_DEBUG, "get_client_side_method() not implemented")
        return server_side_method

    def get_supported_methods(self, obj=None, **kwargs):
        """Returns a name:callable dict of supported platform methods."""

        self._print(self.LOG_DEBUG, "get_supported_methods() not implemented")
        return {}

    def get_bug(self, assertion_string, expected_result, actual_result, **kwargs):
        """Returns a string containing bug information for an assertion."""

        self._print(self.LOG_DEBUG, "get_bug() not implemented")
        return ""

    def get_bug_details(self, bug_uri):
        """Returns a string containing the details of the bug at the specified uri."""

        details = self._bugs.get(bug_uri)
        if details is not None:
            return details

        try:
            response = request.urlopen(bug_uri)
        except:
            return ""

        match = re.search("(?<=\<title\>)(.*)(?=\<\/title\>)", str(response.peek()))
        if match:
            details = html.unescape(match.group()).strip()

        key, valuemap = "bugs", {"title": "summary", "status": "status"}

        uri = response.geturl()
        scheme, netloc, path, query, fragment = parse.urlsplit(uri)
        if path == "/show_bug.cgi" and query.startswith("id="):
            path = "/rest/bug/%s" % query.replace("id=", "")
            query = ""
        elif netloc == "github.com" and "issues" in path:
            netloc = "api.%s" % netloc
            path = "repos%s" % path
            key, valuemap = "", {"title": "title", "status": "state"}

        uri = parse.urlunsplit((scheme, netloc, path, query, fragment))
        try:
            response = request.urlopen(uri)
            data = response.read()
            content = json.loads(data.decode("utf-8"))
            info = content.get(key, content)
            if isinstance(info, list):
                info = info[0]
        except:
            pass
        else:
            status = info.get(valuemap.get("status", "status"), "")
            resolution = info.get(valuemap.get("resolution", "resolution"), "")
            state = " ".join((status, resolution)).strip().upper()
            if state:
                details = "[%s] %s" % (state, info.get(valuemap.get("title"), details))

        self._bugs[bug_uri] = details
        return details

    def string_to_method_and_arguments(self, callable_as_string, **kwargs):
        """Converts callable_as_string into the appropriate callable platform method
        and list of arguments with the appropriate types."""

        self._print(self.LOG_DEBUG, "string_to_method_and_arguments() not implemented")
        return None, []

    def get_result(self, method, arguments, **kwargs):
        """Returns the result of calling method with the specified arguments."""

        self._print(self.LOG_DEBUG, "get_result() not implemented")
        return None

    def get_supported_actions(self, obj, **kwargs):
        """Returns a list of names of supported actions for obj."""

        self._print(self.LOG_DEBUG, "get_supported_actions() not implemented")
        return []

    def get_supported_properties(self, obj, **kwargs):
        """Returns a list of supported platform properties for obj."""

        self._print(self.LOG_DEBUG, "get_supported_properties() not implemented")
        return []

    def get_supported_relation_types(self, obj=None, **kwargs):
        """Returns a list of supported platform relation types."""

        self._print(self.LOG_DEBUG, "get_supported_relation_types() not implemented")
        return []

    def get_event_history(self, **kwargs):
        """Returns the list of accessibility events recorded by this ATTA."""

        return self._event_history

    def string_to_value(self, string, **kwargs):
        """Returns the value (e.g. a platform constant) represented by string."""

        self._print(self.LOG_DEBUG, "string_to_value() not implemented")
        return None

    def platform_type_to_python_type(self, platform_type, **kwargs):
        """Returns the python type associated with the specified platform type."""

        self._print(self.LOG_DEBUG, "platform_type_to_python_type() not implemented")
        return platform_type

    def convert_platform_string(self, platform_string, **kwargs):
        """Returns the platform-independent string for the specified platform string."""

        self._print(self.LOG_DEBUG, "convert_platform_string() not implemented")
        return platform_string

    def type_to_string(self, value, **kwargs):
        """Returns the type of value as a harness-compliant string."""

        value_type = type(value)

        if value_type == str:
            return "String"

        if value_type == bool:
            return "Boolean"

        if value_type in (int, float):
            return "Number"

        if value_type in (tuple, list, set, range, dict):
            return "List"

        return "Undefined"

    def value_to_string(self, value, **kwargs):
        """Returns the string representation of value (e.g. a platform constant)."""

        value_type = type(value)

        if value_type == str:
            return value

        if value_type == bool:
            return str(value).lower()

        if value_type in (int, float):
            return str(value)

        if value_type in (tuple, list, set):
            return value_type(map(self.value_to_string, value))

        if value_type == range:
            return str(range)

        if value_type == dict:
            return {self.value_to_string(k): self.value_to_string(v) for k, v in value.items()}

        return str(value)

    def _on_load_complete(self, data, **kwargs):
        """Callback for the platform's signal that a document has loaded."""

        self._print(self.LOG_DEBUG, "_on_load_complete() not implemented")

    def _on_test_event(self, data, **kwargs):
        """Callback for platform accessibility events the ATTA is testing."""

        self._print(self.LOG_DEBUG, "_on_test_event() not implemented")
