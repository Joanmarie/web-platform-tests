#!/usr/bin/env python3
#
# atta_request_handler
# Optional Request Handler for Accessible Technology Test Adapters
#
# Developed by Joanmarie Diggs (@joanmarie)
# Copyright (c) 2016-2019 Igalia, S.L.
#
# For license information, see:
# https://www.w3.org/Consortium/Legal/2008/04-testsuite-copyright.html

import json
import re
import threading
import time
import traceback

from http.server import BaseHTTPRequestHandler


class AttaRequestHandler(BaseHTTPRequestHandler):
    """Optional request handler for python3 Accessible Technology Test Adapters."""

    _atta = None
    _timeout = 5
    _running_tests = False

    @classmethod
    def set_atta(cls, atta):
        cls._atta = atta

    @classmethod
    def is_running_tests(cls):
        return cls._running_tests

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def dispatch(self):
        if self.path.endswith("start"):
            self.start_test_run()
        elif self.path.endswith("startlisten"):
            self.start_listen()
        elif self.path.endswith("test"):
            self.run_tests()
        elif self.path.endswith("stoplisten"):
            self.stop_listen()
        elif self.path.endswith("end"):
            self.end_test_run()
        else:
            self.send_error(400, "UNHANDLED PATH: %s" % self.path)

    def send_error(self, code, message=None):
        if message is None:
            message = "Error: bad request"

        self.send_response(code, message)
        self.send_header("Content-Type", "text/plain")
        self.add_headers()
        self.wfile.write(bytes("%s\n" % message, "utf-8"))

    @staticmethod
    def dump_json(obj):
        return json.dumps(obj, indent=4, sort_keys=True)

    def add_aria_headers(self):
        self.send_header("Content-Type", "application/json")
        self.add_headers()

    def add_headers(self):
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers", "Allow, Content-Type")
        self.send_header("Allow", "POST")
        self.end_headers()

    def flush_headers(self):
        try:
            super().flush_headers()
        except Exception as error:
            self.wfile._wbuf = []
            self.wfile._wbuf_len = 0
            msg = "Broken pipe in flush_headers(). Manually clearing buffer."
            self._atta.log_message(msg, self._atta.LOG_ERROR)

    def get_params(self, *params):
        submission = {}
        response = {}
        errors = []

        try:
            length = self.headers.__getitem__("content-length")
            content = self.rfile.read(int(length))
            submission = json.loads(content.decode("utf-8"))
        except:
            error = traceback.format_exc(limit=1, chain=False)
            errors.append(error)

        for param in params:
            value = submission.get(param)
            if value is None:
                errors.append("Parameter %s not found" % param)
            else:
                response[param] = value

        response["error"] = "; ".join(errors)
        return response

    def log_error(self, format, *args):
        self._atta.log_message(format % args, self._atta.LOG_ERROR)

    def log_message(self, format, *args):
        self._atta.log_message(format % args, self._atta.LOG_DEBUG)

    def _send_response(self, response, status_code=200):
        if response.get("statusText") is None:
            response["statusText"] = ""

        message = response.get("statusText")
        self.send_response(status_code, message)
        self.add_aria_headers()
        dump = self.dump_json(response)
        try:
            self.wfile.write(bytes(dump, "utf-8"))
        except BrokenPipeError:
            self.wfile._wbuf = []
            self.wfile._wbuf_len = 0
            msg = "Broken pipe in _send_response(). Manually clearing buffer."
            self._atta.log_message(msg, self._atta.LOG_ERROR)
        except Exception as error:
            self._atta.log_message(error, self._atta.LOG_ERROR)

    def _wait(self, start_time, method, response={}):
        if method.__call__():
            return False

        if time.time() - start_time > self._timeout:
            msg = "Timeout waiting for %s() to return True" % method.__name__
            response.update({"status": "ERROR", "statusText": msg})
            self._send_response(response, 500)
            return False

        return True

    def _wait_for_run_request(self):
        class Timer(threading.Thread):
            def __init__(self, timeout, atta):
                super().__init__(daemon=True)
                self.timeout = time.time() + timeout
                self._atta = atta

            def run(self):
                while not AttaRequestHandler.is_running_tests():
                    if time.time() > self.timeout:
                        msg = "'test' request not received from ATTAcomm.js."
                        self._atta.log_message(msg, self._atta.LOG_ERROR)
                        return

        thread = Timer(self._timeout, self._atta)
        thread.start()

    def start_test_run(self):
        AttaRequestHandler._running_tests = False
        response = {}
        params = self.get_params("test", "url")
        error = params.get("error")
        if error:
            response["status"] = "ERROR"
            response["statusText"] = error
            self._send_response(response)
            return

        if not (self._atta and self._atta.is_enabled()):
            response["status"] = "ERROR"
            response["statusText"] = "ENABLED ATTA NOT FOUND. TEST MUST BE RUN MANUALLY."
            self._send_response(response)
            return

        start_time = time.time()
        response.update(self._atta.get_info())
        self._atta.start_test_run(name=params.get("test"), url=params.get("url"))
        while self._wait(start_time, self._atta.is_ready, response):
            time.sleep(0.5)

        if self._atta.is_ready():
            response["status"] = "READY"
            self._send_response(response)
            self._wait_for_run_request()

    def start_listen(self):
        params = self.get_params("events")
        error = params.get("error")
        response = {}
        if error:
            response["status"] = "ERROR"
            response["statusText"] = error
            self._send_response(response)
            return

        if self._atta is not None:
            self._atta.start_listen(params.get("events"))

        response["status"] = "READY"
        self._send_response(response)

    def _apply_markdown(self, string):
        if not string.endswith("\n"):
            string += "\n"

        for match in re.finditer("https*://[^\s\:\;]+", string):
            string = re.sub(match.group(), "<%s>" % match.group(), string)

        string = re.sub("_", "\_", string)
        string = re.sub("\n", "  \n", string)
        string = re.sub("Actual value:", "  \n**Actual value:**", string)
        return string

    def run_tests(self):
        AttaRequestHandler._running_tests = True
        params = self.get_params("title", "id", "data")
        response = {}
        if self._atta is not None:
            result = self._atta.run_tests(params.get("id"), params.get("data", {}))
            response.update(result)

        results = response.get("results", [])
        if not results:
            response["statusText"] = params.get("error")
            self._send_response(response)
            return

        for result in results:
            result["message"] = self._apply_markdown(result.get("message", ""))

        response["results"] = results
        self._send_response(response)

    def stop_listen(self):
        if self._atta is not None:
            self._atta.stop_listen()

        response = {"status": "READY"}
        self._send_response(response)

    def end_test_run(self):
        self._atta.end_test_run()
        response = {"status": "DONE"}
        self._send_response(response)
        AttaRequestHandler._running_tests = False
