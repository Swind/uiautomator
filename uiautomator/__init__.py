#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Python wrapper for Android uiautomator tool."""

import sys
import os
import time
import itertools
import socket
import collections
import xml.dom.minidom
import threading

try:
    import urllib2
except ImportError:
    import urllib.request as urllib2

from ppadb.client import Client as AdbClient

from uiautomator.rpc.json_rpc import jsonrpc_wrap, JsonRPCClient, JsonRPCError
from uiautomator.selector import Selector


DEVICE_PORT = int(os.environ.get('UIAUTOMATOR_DEVICE_PORT', '9008'))
LOCAL_PORT = int(os.environ.get('UIAUTOMATOR_LOCAL_PORT', '9008'))

if 'localhost' not in os.environ.get('no_proxy', ''):
    os.environ['no_proxy'] = "localhost,%s" % os.environ.get('no_proxy', '')

__author__ = "Xiaocong He"
__all__ = ["device", "Device", "rect", "point", "Selector", "JsonRPCError"]


def param_to_property(*props, **kwprops):
    if props and kwprops:
        raise SyntaxError("Can not set both props and kwprops at the same time.")

    class Wrapper(object):

        def __init__(self, func):
            self.func = func
            self.kwargs, self.args = {}, []

        def __getattr__(self, attr):
            if kwprops:
                for prop_name, prop_values in kwprops.items():
                    if attr in prop_values and prop_name not in self.kwargs:
                        self.kwargs[prop_name] = attr
                        return self
            elif attr in props:
                self.args.append(attr)
                return self
            raise AttributeError("%s parameter is duplicated or not allowed!" % attr)

        def __call__(self, *args, **kwargs):
            if kwprops:
                kwargs.update(self.kwargs)
                self.kwargs = {}
                return self.func(*args, **kwargs)
            else:
                new_args, self.args = self.args + list(args), []
                return self.func(*new_args, **kwargs)
    return Wrapper


def rect(top=0, left=0, bottom=100, right=100):
    return {"top": top, "left": left, "bottom": bottom, "right": right}


def intersect(rect1, rect2):
    top = rect1["top"] if rect1["top"] > rect2["top"] else rect2["top"]
    bottom = rect1["bottom"] if rect1["bottom"] < rect2["bottom"] else rect2["bottom"]
    left = rect1["left"] if rect1["left"] > rect2["left"] else rect2["left"]
    right = rect1["right"] if rect1["right"] < rect2["right"] else rect2["right"]
    return left, top, right, bottom


def point(x=0, y=0):
    return {"x": x, "y": y}



_init_local_port = LOCAL_PORT - 1


def next_local_port(adbHost=None):
    def is_port_listening(port):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = s.connect_ex((str(adbHost) if adbHost else '127.0.0.1', port))
        s.close()
        return result == 0
    global _init_local_port
    _init_local_port = _init_local_port + 1 if _init_local_port < 32764 else LOCAL_PORT
    while is_port_listening(_init_local_port):
        _init_local_port += 1
    return _init_local_port


class NotFoundHandler(object):

    '''
    Handler for UI Object Not Found exception.
    It's a replacement of UiAutomator watcher on device side.
    '''

    def __init__(self):
        self.__handlers = collections.defaultdict(lambda: {'on': True, 'handlers': []})

    def __get__(self, instance, type):
        return self.__handlers[instance.adb_device.serial]


class AutomatorServer(object):

    """start and quit rpc server on device.
    """
    __jar_files = {
        "bundle.jar": "libs/bundle.jar",
        "uiautomator-stub.jar": "libs/uiautomator-stub.jar"
    }

    __apk_files = ["libs/app-uiautomator.apk", "libs/app-uiautomator-test.apk"]

    __sdk = 0

    handlers = NotFoundHandler()  # handler UI Not Found exception

    def __init__(self, serial, local_port=None, device_port=None, adb_server_host="127.0.0.1", adb_server_port=5037):
        self.adb_client = AdbClient(host=adb_server_host, port=adb_server_port)
        self.adb_device = self.adb_client.device(serial)

        self._stop_server = False
        self._message_handler_thread = None
        self._uiautomator_connection = None

        self.device_port = int(device_port) if device_port else DEVICE_PORT
        if local_port:
            self.local_port = local_port
        else:
            try:  # first we will try to use the local port already adb forwarded
                for device_local_port, device_remote_port in self.adb_device.list_forward().items():
                    if device_remote_port == 'tcp:{}'.format(self.device_port):
                        self.local_port = int(device_local_port[4:])
                        break
                else:
                    self.local_port = next_local_port(adb_server_host)
            except:
                self.local_port = next_local_port(adb_server_host)

    def push(self):
        base_dir = os.path.dirname(__file__)
        for jar, url in self.__jar_files.items():
            filename = os.path.join(base_dir, url)
            self.adb_device.push(filename, "/data/local/tmp/")
        return list(self.__jar_files.keys())

    def install(self):
        base_dir = os.path.dirname(__file__)
        for apk in self.__apk_files:
            self.adb_device.install(os.path.join(base_dir, apk), reinstall=True, test=True)

    @property
    def jsonrpc(self):
        return jsonrpc_wrap(self, timeout=int(os.environ.get("jsonrpc_timeout", 90)))

    def __jsonrpc(self):
        return JsonRPCClient(self.rpc_uri, timeout=int(os.environ.get("JSONRPC_TIMEOUT", 90)))

    def sdk_version(self):
        '''sdk version of connected device.'''
        if self.__sdk == 0:
            try:
                self.__sdk = int(self.adb_device.shell("getprop ro.build.version.sdk"))
            except:
                pass
        return self.__sdk

    def _handle_server_message(self, connection):
        self._uiautomator_connection = connection
        try:
            while True:
                if not self._stop_server:
                    data = connection.read(length=1024)
                    print(str(data))

        except Exception as e:
            if not self._stop_server:
                raise e

    def handle_server_message(self, connection):
        self._message_handler_thread = threading.Thread(target=self._handle_server_message, args=(connection,), daemon=True)
        self._message_handler_thread.start()


    def start(self, timeout=5):
        if self.sdk_version() < 18:
            files = self.push()
            cmd = list(itertools.chain(
                ["shell", "uiautomator", "runtest"],
                files,
                ["-c", "com.github.uiautomatorstub.Stub"]
            ))
        else:
            self.install()
            cmd = ["am", "instrument", "-w",
                   "com.github.uiautomator.test/android.support.test.runner.AndroidJUnitRunner"]

        self.adb_device.shell(" ".join(cmd), handler=self.handle_server_message)
        self.adb_device.forward("tcp:{}".format(self.local_port), "tcp:{}".format(self.device_port))

        while not self.alive and timeout > 0:
            time.sleep(0.1)
            timeout -= 0.1
        if not self.alive:
            raise IOError("RPC server not started!")

    def ping(self):
        try:
            return self.__jsonrpc().ping()
        except:
            return None

    @property
    def alive(self):
        '''Check if the rpc server is alive.'''
        return self.ping() == "pong"

    def stop(self):
        '''Stop the rpc server.'''
        if self._message_handler_thread:
            self._stop_server = True
            res = None

            try:
                res = urllib2.urlopen(self.stop_uri)
            except:
                if self._uiautomator_connection:
                    self._uiautomator_connection.close()
            finally:
                if res is not None:
                    res.close()

                self._uiautomator_connection = None

        try:
            pid = self.adb_device.get_pid("com.github.uiautomator")
            if pid:
                result = self.adb_device.shell("kill -9 {}".format(pid))
                print(result)
        except:
            pass

    @property
    def stop_uri(self):
        return "http://%s:%d/stop" % (self.adb_client.host, self.local_port)

    @property
    def rpc_uri(self):
        return "http://%s:%d/jsonrpc/0" % (self.adb_client.host, self.local_port)

    @property
    def screenshot_uri(self):
        return "http://%s:%d/screenshot/0" % (self.adb_client.host, self.local_port)

    def screenshot(self, filename=None, scale=1.0, quality=100):
        if self.sdk_version() >= 18:
            try:
                req = urllib2.Request("%s?scale=%f&quality=%f" % (self.screenshot_uri, scale, quality))
                result = urllib2.urlopen(req, timeout=30)
                if filename:
                    with open(filename, 'wb') as f:
                        f.write(result.read())
                        return filename
                else:
                    return result.read()
            except:
                pass
        return None


class AutomatorDevice(object):

    '''uiautomator wrapper of android device'''

    __orientation = (  # device orientation
        (0, "natural", "n", 0),
        (1, "left", "l", 90),
        (2, "upsidedown", "u", 180),
        (3, "right", "r", 270)
    )
    __alias = {
        "width": "displayWidth",
        "height": "displayHeight"
    }

    def __init__(self, serial, local_port=None, adb_server_host="127.0.0.1", adb_server_port=5037):
        self.server = AutomatorServer(
            serial=serial,
            local_port=local_port,
            adb_server_host=adb_server_host,
            adb_server_port=adb_server_port
        )

    def __call__(self, **kwargs):
        return AutomatorDeviceObject(self, Selector(**kwargs))

    def __getattr__(self, attr):
        '''alias of fields in info property.'''
        info = self.info
        if attr in info:
            return info[attr]
        elif attr in self.__alias:
            return info[self.__alias[attr]]
        else:
            raise AttributeError("%s attribute not found!" % attr)

    @property
    def info(self):
        '''Get the device info.'''
        return self.server.jsonrpc.deviceInfo()

    def click(self, x, y):
        '''click at arbitrary coordinates.'''
        return self.server.jsonrpc.click(x, y)

    def long_click(self, x, y):
        '''long click at arbitrary coordinates.'''
        return self.swipe(x, y, x + 1, y + 1)

    def swipe(self, sx, sy, ex, ey, steps=100):
        return self.server.jsonrpc.swipe(sx, sy, ex, ey, steps)

    def swipePoints(self, points, steps=100):
        ppoints = []
        for p in points:
            ppoints.append(p[0])
            ppoints.append(p[1])
        return self.server.jsonrpc.swipePoints(ppoints, steps)

    def drag(self, sx, sy, ex, ey, steps=100):
        '''Swipe from one point to another point.'''
        return self.server.jsonrpc.drag(sx, sy, ex, ey, steps)

    def dump(self, filename=None, compressed=True, pretty=True):
        '''dump device window and pull to local file.'''
        content = self.server.jsonrpc.dumpWindowHierarchy(compressed, None)
        if filename:
            with open(filename, "wb") as f:
                f.write(content.encode("utf-8"))
        if pretty and "\n " not in content:
            xml_text = xml.dom.minidom.parseString(content.encode("utf-8"))
            content = U(xml_text.toprettyxml(indent='  '))
        return content

    def screenshot(self, filename, scale=1.0, quality=100):
        '''take screenshot.'''
        result = self.server.screenshot(filename, scale, quality)
        if result:
            return result

        device_file = self.server.jsonrpc.takeScreenshot("screenshot.png",
                                                         scale, quality)
        if not device_file:
            return None
        self.server.adb_device.pull(device_file, filename)
        self.server.adb_device.shell("rm {}".format(device_file))
        return filename if p.returncode is 0 else None

    def freeze_rotation(self, freeze=True):
        '''freeze or unfreeze the device rotation in current status.'''
        self.server.jsonrpc.freezeRotation(freeze)

    @property
    def orientation(self):
        '''
        orienting the devie to left/right or natural.
        left/l:       rotation=90 , displayRotation=1
        right/r:      rotation=270, displayRotation=3
        natural/n:    rotation=0  , displayRotation=0
        upsidedown/u: rotation=180, displayRotation=2
        '''
        return self.__orientation[self.info["displayRotation"]][1]

    @orientation.setter
    def orientation(self, value):
        '''setter of orientation property.'''
        for values in self.__orientation:
            if value in values:
                # can not set upside-down until api level 18.
                self.server.jsonrpc.setOrientation(values[1])
                break
        else:
            raise ValueError("Invalid orientation.")

    @property
    def last_traversed_text(self):
        '''get last traversed text. used in webview for highlighted text.'''
        return self.server.jsonrpc.getLastTraversedText()

    def clear_traversed_text(self):
        '''clear the last traversed text.'''
        self.server.jsonrpc.clearLastTraversedText()

    @property
    def open(self):
        '''
        Open notification or quick settings.
        Usage:
        d.open.notification()
        d.open.quick_settings()
        '''
        @param_to_property(action=["notification", "quick_settings"])
        def _open(action):
            if action == "notification":
                return self.server.jsonrpc.openNotification()
            else:
                return self.server.jsonrpc.openQuickSettings()
        return _open

    @property
    def handlers(self):
        obj = self

        class Handlers(object):

            def on(self, fn):
                if fn not in obj.server.handlers['handlers']:
                    obj.server.handlers['handlers'].append(fn)
                obj.server.handlers['device'] = obj
                return fn

            def off(self, fn):
                if fn in obj.server.handlers['handlers']:
                    obj.server.handlers['handlers'].remove(fn)

        return Handlers()

    @property
    def watchers(self):
        obj = self

        class Watchers(list):

            def __init__(self):
                for watcher in obj.server.jsonrpc.getWatchers():
                    self.append(watcher)

            @property
            def triggered(self):
                return obj.server.jsonrpc.hasAnyWatcherTriggered()

            def remove(self, name=None):
                if name:
                    obj.server.jsonrpc.removeWatcher(name)
                else:
                    for name in self:
                        obj.server.jsonrpc.removeWatcher(name)

            def reset(self):
                obj.server.jsonrpc.resetWatcherTriggers()
                return self

            def run(self):
                obj.server.jsonrpc.runWatchers()
                return self
        return Watchers()

    def watcher(self, name):
        obj = self

        class Watcher(object):

            def __init__(self):
                self.__selectors = []

            @property
            def triggered(self):
                return obj.server.jsonrpc.hasWatcherTriggered(name)

            def remove(self):
                obj.server.jsonrpc.removeWatcher(name)

            def when(self, **kwargs):
                self.__selectors.append(Selector(**kwargs))
                return self

            def click(self, **kwargs):
                obj.server.jsonrpc.registerClickUiObjectWatcher(name, self.__selectors, Selector(**kwargs))

            @property
            def press(self):
                @param_to_property(
                    "home", "back", "left", "right", "up", "down", "center",
                    "search", "enter", "delete", "del", "recent", "volume_up",
                    "menu", "volume_down", "volume_mute", "camera", "power")
                def _press(*args):
                    obj.server.jsonrpc.registerPressKeyskWatcher(name, self.__selectors, args)
                return _press
        return Watcher()

    @property
    def press(self):
        '''
        press key via name or key code. Supported key name includes:
        home, back, left, right, up, down, center, menu, search, enter,
        delete(or del), recent(recent apps), volume_up, volume_down,
        volume_mute, camera, power.
        Usage:
        d.press.back()  # press back key
        d.press.menu()  # press home key
        d.press(89)     # press keycode
        '''
        @param_to_property(
            key=["home", "back", "left", "right", "up", "down", "center",
                 "menu", "search", "enter", "delete", "del", "recent",
                 "volume_up", "volume_down", "volume_mute", "camera", "power"]
        )
        def _press(key, meta=None):
            if isinstance(key, int):
                return self.server.jsonrpc.pressKeyCode(key, meta) if meta else self.server.jsonrpc.pressKeyCode(key)
            else:
                return self.server.jsonrpc.pressKey(str(key))
        return _press

    def wakeup(self):
        '''turn on screen in case of screen off.'''
        self.server.jsonrpc.wakeUp()

    def sleep(self):
        '''turn off screen in case of screen on.'''
        self.server.jsonrpc.sleep()

    @property
    def screen(self):
        '''
        Turn on/off screen.
        Usage:
        d.screen.on()
        d.screen.off()

        d.screen == 'on'  # Check if the screen is on, same as 'd.screenOn'
        d.screen == 'off'  # Check if the screen is off, same as 'not d.screenOn'
        '''
        devive_self = self

        class _Screen(object):
            def on(self):
                return devive_self.wakeup()

            def off(self):
                return devive_self.sleep()

            def __call__(self, action):
                if action == "on":
                    return self.on()
                elif action == "off":
                    return self.off()
                else:
                    raise AttributeError("Invalid parameter: %s" % action)

            def __eq__(self, value):
                info = devive_self.info
                if "screenOn" not in info:
                    raise EnvironmentError("Not supported on Android 4.3 and belows.")
                if value in ["on", "On", "ON"]:
                    return info["screenOn"]
                elif value in ["off", "Off", "OFF"]:
                    return not info["screenOn"]
                raise ValueError("Invalid parameter. It can only be compared with on/off.")

            def __ne__(self, value):
                return not self.__eq__(value)

        return _Screen()

    @property
    def wait(self):
        '''
        Waits for the current application to idle or window update event occurs.
        Usage:
        d.wait.idle(timeout=1000)
        d.wait.update(timeout=1000, package_name="com.android.settings")
        '''
        @param_to_property(action=["idle", "update"])
        def _wait(action, timeout=1000, package_name=None):
            if timeout / 1000 + 5 > int(os.environ.get("JSONRPC_TIMEOUT", 90)):
                http_timeout = timeout / 1000 + 5
            else:
                http_timeout = int(os.environ.get("JSONRPC_TIMEOUT", 90))
            if action == "idle":
                return jsonrpc_wrap(self.server, timeout=http_timeout).waitForIdle(timeout)
            elif action == "update":
                return jsonrpc_wrap(self.server, timeout=http_timeout).waitForWindowUpdate(package_name, timeout)
        return _wait

    def exists(self, **kwargs):
        '''Check if the specified ui object by kwargs exists.'''
        return self(**kwargs).exists

Device = AutomatorDevice


class AutomatorDeviceUiObject(object):

    '''Represent a UiObject, on which user can perform actions, such as click, set text
    '''

    __alias = {'description': "contentDescription"}

    def __init__(self, device, selector):
        self.device = device
        self.jsonrpc = device.server.jsonrpc
        self.selector = selector

    @property
    def exists(self):
        '''check if the object exists in current window.'''
        return self.jsonrpc.exist(self.selector)

    def __getattr__(self, attr):
        '''alias of fields in info property.'''
        info = self.info
        if attr in info:
            return info[attr]
        elif attr in self.__alias:
            return info[self.__alias[attr]]
        else:
            raise AttributeError("%s attribute not found!" % attr)

    @property
    def info(self):
        '''ui object info.'''
        return self.jsonrpc.objInfo(self.selector)

    def set_text(self, text):
        '''set the text field.'''
        if text in [None, ""]:
            return self.jsonrpc.clearTextField(self.selector)  # TODO no return
        else:
            return self.jsonrpc.setText(self.selector, text)

    def clear_text(self):
        '''clear text. alias for set_text(None).'''
        self.set_text(None)

    @property
    def click(self):
        '''
        click on the ui object.
        Usage:
        d(text="Clock").click()  # click on the center of the ui object
        d(text="OK").click.wait(timeout=3000) # click and wait for the new window update
        d(text="John").click.topleft() # click on the topleft of the ui object
        d(text="John").click.bottomright() # click on the bottomright of the ui object
        '''
        @param_to_property(action=["tl", "topleft", "br", "bottomright", "wait"])
        def _click(action=None, timeout=3000):
            if action is None:
                return self.jsonrpc.click(self.selector)
            elif action in ["tl", "topleft", "br", "bottomright"]:
                return self.jsonrpc.click(self.selector, action)
            else:
                return self.jsonrpc.clickAndWaitForNewWindow(self.selector, timeout)
        return _click

    @property
    def long_click(self):
        '''
        Perform a long click action on the object.
        Usage:
        d(text="Image").long_click()  # long click on the center of the ui object
        d(text="Image").long_click.topleft()  # long click on the topleft of the ui object
        d(text="Image").long_click.bottomright()  # long click on the topleft of the ui object
        '''
        @param_to_property(corner=["tl", "topleft", "br", "bottomright"])
        def _long_click(corner=None):
            info = self.info
            if info["longClickable"]:
                if corner:
                    return self.jsonrpc.longClick(self.selector, corner)
                else:
                    return self.jsonrpc.longClick(self.selector)
            else:
                bounds = info.get("visibleBounds") or info.get("bounds")
                if corner in ["tl", "topleft"]:
                    x = (5 * bounds["left"] + bounds["right"]) / 6
                    y = (5 * bounds["top"] + bounds["bottom"]) / 6
                elif corner in ["br", "bottomright"]:
                    x = (bounds["left"] + 5 * bounds["right"]) / 6
                    y = (bounds["top"] + 5 * bounds["bottom"]) / 6
                else:
                    x = (bounds["left"] + bounds["right"]) / 2
                    y = (bounds["top"] + bounds["bottom"]) / 2
                return self.device.long_click(x, y)
        return _long_click

    @property
    def drag(self):
        '''
        Drag the ui object to other point or ui object.
        Usage:
        d(text="Clock").drag.to(x=100, y=100)  # drag to point (x,y)
        d(text="Clock").drag.to(text="Remove") # drag to another object
        '''
        def to(obj, *args, **kwargs):
            if len(args) >= 2 or "x" in kwargs or "y" in kwargs:
                drag_to = lambda x, y, steps=100: self.jsonrpc.dragTo(self.selector, x, y, steps)
            else:
                drag_to = lambda steps=100, **kwargs: self.jsonrpc.dragTo(self.selector, Selector(**kwargs), steps)
            return drag_to(*args, **kwargs)
        return type("Drag", (object,), {"to": to})()

    def gesture(self, start1, start2, *args, **kwargs):
        '''
        perform two point gesture.
        Usage:
        d().gesture(startPoint1, startPoint2).to(endPoint1, endPoint2, steps)
        d().gesture(startPoint1, startPoint2, endPoint1, endPoint2, steps)
        '''
        def to(obj_self, end1, end2, steps=100):
            ctp = lambda pt: point(*pt) if type(pt) == tuple else pt  # convert tuple to point
            s1, s2, e1, e2 = ctp(start1), ctp(start2), ctp(end1), ctp(end2)
            return self.jsonrpc.gesture(self.selector, s1, s2, e1, e2, steps)
        obj = type("Gesture", (object,), {"to": to})()
        return obj if len(args) == 0 else to(None, *args, **kwargs)

    def gestureM(self, start1, start2, start3, *args, **kwargs):
        '''
        perform 3 point gesture.
        Usage:
        d().gestureM((100,200),(100,300),(100,400),(100,400),(100,400),(100,400))
        d().gestureM((100,200),(100,300),(100,400)).to((100,400),(100,400),(100,400))
        '''
        def to(obj_self, end1, end2, end3, steps=100):
            ctp = lambda pt: point(*pt) if type(pt) == tuple else pt  # convert tuple to point
            s1, s2, s3, e1, e2, e3 = ctp(start1), ctp(start2), ctp(start3), ctp(end1), ctp(end2), ctp(end3)
            return self.jsonrpc.gesture(self.selector, s1, s2, s3, e1, e2, e3, steps)
        obj = type("Gesture", (object,), {"to": to})()
        return obj if len(args) == 0 else to(None, *args, **kwargs)

    @property
    def pinch(self):
        '''
        Perform two point gesture from edge to center(in) or center to edge(out).
        Usages:
        d().pinch.In(percent=100, steps=10)
        d().pinch.Out(percent=100, steps=100)
        '''
        @param_to_property(in_or_out=["In", "Out"])
        def _pinch(in_or_out="Out", percent=100, steps=50):
            if in_or_out in ["Out", "out"]:
                return self.jsonrpc.pinchOut(self.selector, percent, steps)
            elif in_or_out in ["In", "in"]:
                return self.jsonrpc.pinchIn(self.selector, percent, steps)
        return _pinch

    @property
    def swipe(self):
        '''
        Perform swipe action. if device platform greater than API 18, percent can be used and value between 0 and 1
        Usages:
        d().swipe.right()
        d().swipe.left(steps=10)
        d().swipe.up(steps=10)
        d().swipe.down()
        d().swipe("right", steps=20)
        d().swipe("right", steps=20, percent=0.5)
        '''
        @param_to_property(direction=["up", "down", "right", "left"])
        def _swipe(direction="left", steps=10, percent=1):
            if percent == 1:
                return self.jsonrpc.swipe(self.selector, direction, steps)
            else:
                return self.jsonrpc.swipe(self.selector, direction, percent, steps)
        return _swipe

    @property
    def wait(self):
        '''
        Wait until the ui object gone or exist.
        Usage:
        d(text="Clock").wait.gone()  # wait until it's gone.
        d(text="Settings").wait.exists() # wait until it appears.
        '''
        @param_to_property(action=["exists", "gone"])
        def _wait(action, timeout=3000):
            if timeout / 1000 + 5 > int(os.environ.get("JSONRPC_TIMEOUT", 90)):
                http_timeout = timeout / 1000 + 5
            else:
                http_timeout = int(os.environ.get("JSONRPC_TIMEOUT", 90))
            method = jsonrpc_wrap(
                self.device.server,
                timeout=http_timeout
            ).waitUntilGone if action == "gone" else jsonrpc_wrap(self.device.server, timeout=http_timeout).waitForExists
            return method(self.selector, timeout)
        return _wait


class AutomatorDeviceNamedUiObject(AutomatorDeviceUiObject):

    def __init__(self, device, name):
        super(AutomatorDeviceNamedUiObject, self).__init__(device, name)

    def child(self, **kwargs):
        return AutomatorDeviceNamedUiObject(
            self.device,
            self.jsonrpc.getChild(self.selector, Selector(**kwargs))
        )

    def sibling(self, **kwargs):
        return AutomatorDeviceNamedUiObject(
            self.device,
            self.jsonrpc.getFromParent(self.selector, Selector(**kwargs))
        )


class AutomatorDeviceObject(AutomatorDeviceUiObject):

    '''Represent a generic UiObject/UiScrollable/UiCollection,
    on which user can perform actions, such as click, set text
    '''

    def __init__(self, device, selector):
        super(AutomatorDeviceObject, self).__init__(device, selector)

    def child(self, **kwargs):
        '''set childSelector.'''
        return AutomatorDeviceObject(
            self.device,
            self.selector.clone().child(**kwargs)
        )

    def sibling(self, **kwargs):
        '''set fromParent selector.'''
        return AutomatorDeviceObject(
            self.device,
            self.selector.clone().sibling(**kwargs)
        )

    child_selector, from_parent = child, sibling

    def child_by_text(self, txt, **kwargs):
        if "allow_scroll_search" in kwargs:
            allow_scroll_search = kwargs.pop("allow_scroll_search")
            name = self.jsonrpc.childByText(
                self.selector,
                Selector(**kwargs),
                txt,
                allow_scroll_search
            )
        else:
            name = self.jsonrpc.childByText(
                self.selector,
                Selector(**kwargs),
                txt
            )
        return AutomatorDeviceNamedUiObject(self.device, name)

    def child_by_description(self, txt, **kwargs):
        if "allow_scroll_search" in kwargs:
            allow_scroll_search = kwargs.pop("allow_scroll_search")
            name = self.jsonrpc.childByDescription(
                self.selector,
                Selector(**kwargs),
                txt,
                allow_scroll_search
            )
        else:
            name = self.jsonrpc.childByDescription(
                self.selector,
                Selector(**kwargs),
                txt
            )
        return AutomatorDeviceNamedUiObject(self.device, name)

    def child_by_instance(self, inst, **kwargs):
        return AutomatorDeviceNamedUiObject(
            self.device,
            self.jsonrpc.childByInstance(self.selector, Selector(**kwargs), inst)
        )

    @property
    def count(self):
        return self.jsonrpc.count(self.selector)

    def __len__(self):
        return self.count

    def __getitem__(self, index):
        count = self.count
        if index >= count:
            raise IndexError()
        elif count == 1:
            return self
        else:
            selector = self.selector.clone()
            selector["instance"] = index
            return AutomatorDeviceObject(self.device, selector)

    def __iter__(self):
        obj, length = self, self.count

        class Iter(object):

            def __init__(self):
                self.index = -1

            def next(self):
                self.index += 1
                if self.index < length:
                    return obj[self.index]
                else:
                    raise StopIteration()
            __next__ = next

        return Iter()

    def right(self, **kwargs):
        def onrightof(rect1, rect2):
            left, top, right, bottom = intersect(rect1, rect2)
            return rect2["left"] - rect1["right"] if top < bottom else -1
        return self.__view_beside(onrightof, **kwargs)

    def left(self, **kwargs):
        def onleftof(rect1, rect2):
            left, top, right, bottom = intersect(rect1, rect2)
            return rect1["left"] - rect2["right"] if top < bottom else -1
        return self.__view_beside(onleftof, **kwargs)

    def up(self, **kwargs):
        def above(rect1, rect2):
            left, top, right, bottom = intersect(rect1, rect2)
            return rect1["top"] - rect2["bottom"] if left < right else -1
        return self.__view_beside(above, **kwargs)

    def down(self, **kwargs):
        def under(rect1, rect2):
            left, top, right, bottom = intersect(rect1, rect2)
            return rect2["top"] - rect1["bottom"] if left < right else -1
        return self.__view_beside(under, **kwargs)

    def __view_beside(self, onsideof, **kwargs):
        bounds = self.info["bounds"]
        min_dist, found = -1, None
        for ui in AutomatorDeviceObject(self.device, Selector(**kwargs)):
            dist = onsideof(bounds, ui.info["bounds"])
            if dist >= 0 and (min_dist < 0 or dist < min_dist):
                min_dist, found = dist, ui
        return found

    @property
    def fling(self):
        '''
        Perform fling action.
        Usage:
        d().fling()  # default vertically, forward
        d().fling.horiz.forward()
        d().fling.vert.backward()
        d().fling.toBeginning(max_swipes=100) # vertically
        d().fling.horiz.toEnd()
        '''
        @param_to_property(
            dimention=["vert", "vertically", "vertical", "horiz", "horizental", "horizentally"],
            action=["forward", "backward", "toBeginning", "toEnd"]
        )
        def _fling(dimention="vert", action="forward", max_swipes=1000):
            vertical = dimention in ["vert", "vertically", "vertical"]
            if action == "forward":
                return self.jsonrpc.flingForward(self.selector, vertical)
            elif action == "backward":
                return self.jsonrpc.flingBackward(self.selector, vertical)
            elif action == "toBeginning":
                return self.jsonrpc.flingToBeginning(self.selector, vertical, max_swipes)
            elif action == "toEnd":
                return self.jsonrpc.flingToEnd(self.selector, vertical, max_swipes)

        return _fling

    @property
    def scroll(self):
        '''
        Perfrom scroll action.
        Usage:
        d().scroll(steps=50) # default vertically and forward
        d().scroll.horiz.forward(steps=100)
        d().scroll.vert.backward(steps=100)
        d().scroll.horiz.toBeginning(steps=100, max_swipes=100)
        d().scroll.vert.toEnd(steps=100)
        d().scroll.horiz.to(text="Clock")
        '''
        def __scroll(vertical, forward, steps=100):
            method = self.jsonrpc.scrollForward if forward else self.jsonrpc.scrollBackward
            return method(self.selector, vertical, steps)

        def __scroll_to_beginning(vertical, steps=100, max_swipes=1000):
            return self.jsonrpc.scrollToBeginning(self.selector, vertical, max_swipes, steps)

        def __scroll_to_end(vertical, steps=100, max_swipes=1000):
            return self.jsonrpc.scrollToEnd(self.selector, vertical, max_swipes, steps)

        def __scroll_to(vertical, **kwargs):
            return self.jsonrpc.scrollTo(self.selector, Selector(**kwargs), vertical)

        @param_to_property(
            dimention=["vert", "vertically", "vertical", "horiz", "horizental", "horizentally"],
            action=["forward", "backward", "toBeginning", "toEnd", "to"])
        def _scroll(dimention="vert", action="forward", **kwargs):
            vertical = dimention in ["vert", "vertically", "vertical"]
            if action in ["forward", "backward"]:
                return __scroll(vertical, action == "forward", **kwargs)
            elif action == "toBeginning":
                return __scroll_to_beginning(vertical, **kwargs)
            elif action == "toEnd":
                return __scroll_to_end(vertical, **kwargs)
            elif action == "to":
                return __scroll_to(vertical, **kwargs)
        return _scroll
