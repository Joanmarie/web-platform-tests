#!/usr/bin/env python3
#
# axapi_atta
#
# Accessible Technology Test Adapter for AXAPI
#
# Developed by Joanmarie Diggs (@joanmarie)
# Copyright (c) 2017-2019 Igalia, S.L.
#
# For license information, see:
# https://www.w3.org/Consortium/Legal/2008/04-testsuite-copyright.html

import objc
import re
import sys
import time

import ApplicationServices
from PyObjCTools import AppHelper

from atta_base import Atta
from atta_assertion import AttaAssertion


class Listener:
    atta = None

    def __init__(self, atta):
        Listener.atta = atta

    @objc.callbackFor(ApplicationServices.AXObserverCreateWithInfoCallback)
    def on_event(observer, element, notification, info, data):
        if notification == "AXLoadComplete":
            Listener.atta._on_load_complete(element)
            return

        event = {"obj": element, "type": notification}
        if isinstance(info, ApplicationServices.NSDictionary):
            event.update(info)

        Listener.atta._on_test_event(event)


class AxapiAtta(Atta):
    """Accessible Technology Test Adapter to test AXAPI support."""

    AX_ERRORS = {
        ApplicationServices.kAXErrorActionUnsupported: "Action Unsupported",
        ApplicationServices.kAXErrorNotEnoughPrecision: "Not Enough Precision",
        ApplicationServices.kAXErrorAPIDisabled: "API Disabled",
        ApplicationServices.kAXErrorNotificationAlreadyRegistered: "Notification Already Registered",
        ApplicationServices.kAXErrorAttributeUnsupported: "Attribute Unsupported",
        ApplicationServices.kAXErrorNotificationNotRegistered: "Notification Not Registered",
        ApplicationServices.kAXErrorCannotComplete: "Cannot Complete",
        ApplicationServices.kAXErrorNotificationUnsupported: "Notification Unsupported",
        ApplicationServices.kAXErrorFailure: "Failure",
        ApplicationServices.kAXErrorNotImplemented: "Not Implemented",
        ApplicationServices.kAXErrorIllegalArgument: "Illegal Argument",
        ApplicationServices.kAXErrorNoValue: "No Value",
        ApplicationServices.kAXErrorInvalidUIElement: "Invalid UI Element",
        ApplicationServices.kAXErrorParameterizedAttributeUnsupported: "Parameterized Attribute Unsupported",
        ApplicationServices.kAXErrorInvalidUIElementObserver: "Invalid UI Element Observer",
        ApplicationServices.kAXErrorSuccess: "Success"
     }

    def __init__(self, name, version, api):
        """Initializes this ATTA."""

        self._user_agents = {}
        self._axobservers = {}

        wnc = ApplicationServices.NSWorkspaceNotificationCenter.defaultCenter()
        wnc.addObserver_selector_name_object_(self, '_on_app_activated:', 'NSWorkspaceDidActivateApplicationNotification', None)
        super().__init__(name, version, api)

    def start(self, **kwargs):
        """Starts this ATTA (i.e. before running a series of tests)."""

        if not self._enabled:
            return

        # Can we register for app activation notifications here?
        super().start(**kwargs)

    def start_listen(self, event_types, **kwargs):
        """Causes the ATTA to start listening for the specified events."""

        error, pid = ApplicationServices.AXUIElementGetPid(self._current_document, None)
        axapp = self._current_application
        if axapp is None:
            axapp = self._current_application = self._get_axapp_for_document(self._current_document)
        super().start_listen(event_types, pid=pid, axapp=axapp)

    def stop_listen(self, **kwargs):
        """Causes the ATTA to stop listening for the specified events."""

        error, pid = ApplicationServices.AXUIElementGetPid(self._current_document, None)
        axapp = self._current_application
        if axapp is None:
            axapp = self._current_application = self._get_axapp_for_document(self._current_document)
        super().stop_listen(pid=pid, axapp=axapp)

    def shutdown(self, signum=None, frame=None, **kwargs):
        """Shuts down this ATTA (i.e. after all tests have been run)."""

        if not self._enabled:
            return

        if self._load_notifications:
            for pid, axapp in self._user_agents.items():
                self._deregister_listener("AXLoadComplete", self._on_load_complete, pid=pid, axapp=axapp)

        # What else needs to be cleaned up? The app-activated stuff probably....
        self._axobservers = {}
        super().shutdown(signum, frame, **kwargs)

        # Hack until the thread stuff is worked out
        sys.exit(0)

    def _get_accessibility_enabled(self, **kwargs):
        """Returns True if accessibility support is enabled on this platform."""

        # TODO: Implement this
        super()._get_accessibility_enabled(**kwargs)
        return True

    def _set_accessibility_enabled(self, enable, **kwargs):
        """Returns True if accessibility support was successfully set."""

        # TODO: Implement this
        super()._set_accessibility_enabled(**kwargs)
        return True

    def _register_listener(self, event_type, callback, **kwargs):
        """Registers an accessible-event listener on the platform."""

        pid = kwargs.get("pid")
        observer = self._axobservers.get(pid)
        if not observer:
            listener = Listener(self)
            error, observer = ApplicationServices.AXObserverCreateWithInfoCallback(pid, listener.on_event, None)

        axapp = kwargs.get("axapp")

        result = ApplicationServices.AXObserverAddNotification(observer, axapp, event_type, None)
        if result != ApplicationServices.kAXErrorSuccess:
            msg = "Could not register for %s (axapp: %s): %s" % (event_type, axapp, self.AX_ERRORS.get(result))
            self._print(self.LOG_ERROR, msg)
        else:
            msg = "Registered for %s (axapp: %s)" % (event_type, axapp)
            self._print(self.LOG_DEBUG, msg)

        observer_run_loop = ApplicationServices.AXObserverGetRunLoopSource(observer)
        current_run_loop = ApplicationServices.CFRunLoopGetMain()
        ApplicationServices.CFRunLoopAddSource(current_run_loop, observer_run_loop, ApplicationServices.kCFRunLoopDefaultMode)
        self._axobservers[pid] = observer

    def _deregister_listener(self, event_type, callback, **kwargs):
        """De-registers an accessible-event listener on the platform."""

        pid = kwargs.get("pid")
        observer = self._axobservers.get(pid)
        if not observer:
            return

        axapp = kwargs.get("axapp")
        result = ApplicationServices.AXObserverRemoveNotification(observer, axapp, event_type)
        if result != ApplicationServices.kAXErrorSuccess:
            msg = "Could not deregister for %s (axapp: %s): %s" % (event_type, axapp, self.AX_ERRORS.get(result))
            self._print(self.LOG_ERROR, msg)
        else:
            msg = "Deregistered for %s (axapp: %s)" % (event_type, axapp)
            self._print(self.LOG_DEBUG, msg)

    def _is_null_or_defunct(self, obj):
        """Returns True if obj is null or believed to be defunct or generally useless."""

        if obj is None:
            self._print(self.LOG_DEBUG, "Object is null")
            return True

        if not self.get_supported_properties(obj):
            self._print(self.LOG_DEBUG, "%s is believed to be defunct" % obj)
            return True

        return False

    def _get_id(self, obj, **kwargs):
        """Returns the element id associated with obj or an empty string upon failure."""

        if self._is_null_or_defunct(obj):
            return ""

        return self.get_property_value(obj, "AXDOMIdentifier")

    def _get_uri(self, document, **kwargs):
        """Returns the URI associated with document or an empty string upon failure."""

        if self._is_null_or_defunct(document):
            axapp = self._get_axapp_for_document(document)
            document = self._find_descendant(axapp, self._is_document)
            if self._is_null_or_defunct(document):
                return ""

        for name in (ApplicationServices.NSAccessibilityURLAttribute, "AXDocumentURI"):
            uri = self.get_property_value(document, name)
            if uri:
                return str(uri)

        return ""

    def _get_children(self, obj, **kwargs):
        """Returns the children of obj or [] upon failure or absence of children."""

        return self.get_property_value(obj, ApplicationServices.NSAccessibilityChildrenAttribute) or []

    def _get_parent(self, obj, **kwargs):
        """Returns the parent of obj or None upon failure."""

        return self.get_property_value(obj, ApplicationServices.NSAccessibilityParentAttribute)

    def _use_value_in_alternative_name_source(self, obj, value, **kwargs):
        role = self.get_property_value(obj, ApplicationServices.NSAccessibilityRoleAttribute)
        roles = ["AXCheckBox", "AXRadioButton"]
        if role in roles:
            return not value.isnumeric()

        return True

    def _get_name_from_alternative_source(self, obj, original_source, **kwargs):
        """Looks for an accessible name in a source other than original_source."""

        if original_source == "AXDescription":
            source = "AXTitle"
        elif original_source == "AXTitle":
            source = "AXDescription"
        else:
            return None

        error, value = ApplicationServices.AXUIElementCopyAttributeValue(obj, source, None)
        if isinstance(value, str) and value.strip():
            self._print(self.LOG_WARNING, "Falling back on %s. Value: '%s'" % (source, value))
            return value

        error, element = ApplicationServices.AXUIElementCopyAttributeValue(obj, "AXTitleUIElement", None)
        if not element:
            return ""

        error, desc = ApplicationServices.AXUIElementCopyAttributeValue(element, "AXDescription", None)
        error, title = ApplicationServices.AXUIElementCopyAttributeValue(element, "AXTitle", None)
        children = self._get_children(element)
        msg = "AXTitleUIElement (AXDescription: '%s', AXTitle: '%s') has %i children." \
            % (desc, title, len(children))
        value = desc or title
        if isinstance(value, str) and value.strip():
            self._print(self.LOG_WARNING, "Returning name from AXTitleUIElement.")
            return value

        names = []
        for i, child in enumerate(children):
            # This attempts to resolve presentable information similar to the results obtained
            # by VoiceOver. This is needed when there is neither an AXDescription nor an AXTitle
            # but only an AXTitleUIElement.
            cDesc = ApplicationServices.AXUIElementCopyAttributeValue(child, "AXDescription", None)[1]
            cTitle = ApplicationServices.AXUIElementCopyAttributeValue(child, "AXTitle", None)[1]
            cValueDesc = ApplicationServices.AXUIElementCopyAttributeValue(child, "AXValueDescription", None)[1]
            cValue = str(ApplicationServices.AXUIElementCopyAttributeValue(child, "AXValue", None)[1])
            if not self._use_value_in_alternative_name_source(child, cValue):
                cValue = ""
            msg += "\n  %i. AXDescription: '%s', AXTitle: '%s', AXValueDescription: '%s', AXValue: '%s'" \
                % (i, cDesc, cTitle, cValueDesc, cValue)
            cName = cDesc or cTitle or cValueDesc or cValue or ""
            if cName.strip():
                names.append(cName.strip())

        self._print(self.LOG_WARNING, msg)
        value = " ".join(names)
        return value

    def get_property_value(self, obj, property_name, **kwargs):
        """Returns the value of property_name for obj."""

        if property_name == "accessible":
            return obj is not None

        if not obj:
            raise AttributeError("Object not found")

        if property_name == "actions":
            return self.get_supported_actions(obj)

        key = None
        if "." in property_name:
            property_name, key = property_name.split(r".", maxsplit=1)

        error, value = ApplicationServices.AXUIElementCopyAttributeValue(obj, property_name, None)
        if not value and property_name in ["AXDescription", "AXTitle"] and self._any_name_source:
            value = self._get_name_from_alternative_source(obj, property_name)

        if not key:
            return value

        if ApplicationServices.AXValueGetType(value) == ApplicationServices.kAXValueIllegalType:
            return value

        # TODO: According to https://pythonhosted.org/pyobjc/apinotes/ApplicationServices.html,
        # "AXValueGetValue is not yet supported, it requires a manual wrapper." We need to create
        # that wrapper. But we also need to make progress on testing, so.... Sad hack is sad.
        values = dict(v.split(r":", 1) for v in re.findall(r"\w+:\S+", value.description()))
        return values.get(key, value)

    def get_supported_methods(self, obj=None, **kwargs):
        """Returns a name:callable dict of supported platform methods."""

        # TODO: Support the rest.
        return {"AXUIElementIsAttributeSettable": ApplicationServices.AXUIElementIsAttributeSettable}

    def string_to_method_and_arguments(self, callable_as_string, **kwargs):
        """Converts callable_as_string into the appropriate callable platform method
        and list of arguments with the appropriate types."""

        try:
            method_string, args_string = re.split("\(", callable_as_string, maxsplit=1)
            args_string = args_string[:-1]
        except ValueError:
            method_string = callable_as_string
            args_list = []
        else:
            args_list = list(filter(lambda x: x != "", args_string.split(",")))

        supported_methods = self.get_supported_methods()
        method = supported_methods.get(method_string)
        if not method:
            raise NameError("%s is not supported" % method_string)

        # TODO: If possible, programmatically identify and remove out args,
        # along with in args which don't make sense in pyobjc. With respect
        # to the latter, so far they all require "None" in place of a valid
        # pointer, so ....
        args_list.append(None)

        return method, args_list

    def get_result(self, method, arguments, **kwargs):
        """Returns the result of calling method with the specified arguments."""

        obj = kwargs.get("obj")
        if obj:
            arguments.insert(0, obj)

        error, result = method(*arguments)
        if error != ApplicationServices.kAXErrorSuccess:
            raise Exception("%s" % self.AX_ERRORS.get(error))

        return result

    def get_supported_actions(self, obj, **kwargs):
        """Returns a list of names of supported actions for obj."""

        error, value = ApplicationServices.AXUIElementCopyActionNames(obj, None)
        if error == ApplicationServices.kAXErrorSuccess:
            return value

        return []

    def get_supported_properties(self, obj, **kwargs):
        """Returns a list of supported platform properties for obj."""

        property_names = []

        error, names = ApplicationServices.AXUIElementCopyAttributeNames(obj, None)
        if error == ApplicationServices.kAXErrorSuccess:
            property_names.extend(names)

#        error, names = ApplicationServices.AXUIElementCopyParameterizedAttributeNames(obj, None)
#        if error == ApplicationServices.kAXErrorSuccess:
#            property_names.extend(names)

        return property_names

    def convert_platform_string(self, platform_string, **kwargs):
        """Returns the platform-independent string for the specified platform string."""

        if platform_string == "<nil>":
            return "None"
        if platform_string == "YES":
            return "true"
        if platform_string == "NO":
            return "false"

        return platform_string

    def value_to_string(self, value, **kwargs):
        """Returns the string representation of value (e.g. a platform constant)."""

        if isinstance(value, ApplicationServices.AXUIElementRef):
            return self._get_id(value, **kwargs) \
                or self.get_property_value(value, ApplicationServices.NSAccessibilityRoleAttribute)

        if isinstance(value, ApplicationServices.NSArray):
            value = list(value)

        return super().value_to_string(value, **kwargs)

    def _get_axapp_for_document(self, document):
        """Attempts to create an accessible app instance for document."""

        if document is None:
            self._print(self.LOG_ERROR, "Could not get axapp because document is null.")
            return None

        error, pid = ApplicationServices.AXUIElementGetPid(document, None)
        if error != ApplicationServices.kAXErrorSuccess:
            msg = "Could not get pid for document: %s" % self.AX_ERRORS.get(result)

        axapp = ApplicationServices.AXUIElementCreateApplication(pid)
        if axapp is None:
            self._print(self.LOG_ERROR, "AXUIElementCreateApplication failed for pid: %s." % pid)
            return None

        self._print(self.LOG_DEBUG, "AXApp for pid: %s is: %s" % (pid, axapp))
        return axapp

    def _on_app_activated_(self, notification):
        info = notification.userInfo() or {}
        app = info.get("NSWorkspaceApplicationKey")
        name = app.localizedName()
        bundleId = app.bundleIdentifier()
        pid = app.processIdentifier()
        axapp = ApplicationServices.AXUIElementCreateApplication(pid)
        webarea = self._find_descendant(axapp, self._is_document)
        if not webarea:
            return

        if not self._user_agents.get(pid):
            if self._load_notifications:
                self._register_listener("AXLoadComplete", self._on_load_complete, pid=pid, axapp=axapp)
            self._user_agents[pid] = axapp

    def _get_running_applications(self, **kwargs):
        """Returns a list of running accessible-application objects."""

        workspace = ApplicationServices.NSWorkspace.sharedWorkspace()
        pids = map(lambda x: x.processIdentifier(), workspace.runningApplications())
        return list(map(ApplicationServices.AXUIElementCreateApplication, pids))

    def _get_title_property_names(self, **kwargs):
        """Returns a list of property names to be used to obtain an object's title."""

        return [ApplicationServices.NSAccessibilityTitleAttribute]

    def _is_document(self, obj, **kwargs):
        """Returns True if obj is a web document."""

        if obj is None:
            return False

        role = self.get_property_value(obj, ApplicationServices.NSAccessibilityRoleAttribute)
        return role == "AXWebArea"

    def _get_rendering_engine(self, **kwargs):
        """Returns a string with details of the user agent's rendering engine."""

        if not self._current_document:
            return ""

        # TODO: Can we get the rendering engine from the accessible web area?

        error, pid = ApplicationServices.AXUIElementGetPid(self._current_document, None)
        if error != ApplicationServices.kAXErrorSuccess:
            msg = "Could not get pid for current document: %s" % self.AX_ERRORS.get(result)
            self._print(self.LOG_ERROR, msg)

        app = ApplicationServices.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None:
            msg = "Could not get app for pid %s." % pid
            self._print(self.LOG_ERROR, msg)

        bundle_identifier = app.bundleIdentifier()
        if "Safari" in bundle_identifier:
            return "WebKit"
        if "Chrome" in bundle_identifier:
            return "Blink"

        self._print(self.LOG_WARNING, "Unknown user agent %s." % bundle_identifier)
        return ""

    def _on_load_complete(self, data, **kwargs):
        """Callback for the platform's signal that a document has loaded."""

        if self._next_test == (None, ""):
            time.sleep(0.5)

        if self._next_test == (None, ""):
            return

        if self.is_ready(data):
            error, pid = ApplicationServices.AXUIElementGetPid(data, None)
            self._current_application = self._user_agents.get(pid)

    def _on_test_event(self, data, **kwargs):
        """Callback for platform accessibility events the ATTA is testing."""

        if not self._in_current_document(data.get("obj")):
            return

        self._event_history.append(data)

    def get_bug(self, assertion_string, expected_result, actual_result, **kwargs):
        """Returns a string containing bug information for an assertion."""

        return ""


if __name__ == "__main__":
    name = "ATTA for AXAPI"
    version = "0.1"
    api = "AXAPI"

    axapi_atta = AxapiAtta(name, version, api)
    if not axapi_atta.is_enabled():
        sys.exit(1)

    axapi_atta.start()
    AppHelper.runConsoleEventLoop()
