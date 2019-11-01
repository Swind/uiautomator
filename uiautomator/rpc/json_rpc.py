import os
import json
import hashlib
import time
import socket

try:
    import urllib2
except ImportError:
    import urllib.request as urllib2

try:
    from httplib import HTTPException
except:
    from http.client import HTTPException
try:
    if os.name == 'nt':
        import urllib3
except:  # to fix python setup error on Windows.
    pass


class JsonRPCError(Exception):

    def __init__(self, code, message):
        self.code = int(code)
        self.message = message

    def __str__(self):
        return "JsonRPC Error code: %d, Message: %s" % (self.code, self.message)


class JsonRPCMethod(object):

    if os.name == 'nt':
        try:
            pool = urllib3.PoolManager()
        except:
            pass

    def __init__(self, url, method, timeout=30):
        self.url, self.method, self.timeout = url, method, timeout

    def __call__(self, *args, **kwargs):
        if args and kwargs:
            raise SyntaxError("Could not accept both *args and **kwargs as JSONRPC parameters.")
        data = {"jsonrpc": "2.0", "method": self.method, "id": self.id()}
        if args:
            data["params"] = args
        elif kwargs:
            data["params"] = kwargs
        jsonresult = {"result": ""}
        if os.name == "nt":
            res = self.pool.urlopen("POST",
                                    self.url,
                                    headers={"Content-Type": "application/json"},
                                    body=json.dumps(data).encode("utf-8"),
                                    timeout=self.timeout)
            jsonresult = json.loads(res.data.decode("utf-8"))
        else:
            result = None
            try:
                req = urllib2.Request(self.url,
                                      json.dumps(data).encode("utf-8"),
                                      {"Content-type": "application/json"})
                result = urllib2.urlopen(req, timeout=self.timeout)
                jsonresult = json.loads(result.read().decode("utf-8"))
            finally:
                if result is not None:
                    result.close()
        if "error" in jsonresult and jsonresult["error"]:
            raise JsonRPCError(
                jsonresult["error"]["code"],
                "%s: %s" % (jsonresult["error"]["data"]["exceptionTypeName"], jsonresult["error"]["message"])
            )
        return jsonresult["result"]

    def id(self):
        m = hashlib.md5()
        m.update(("%s at %f" % (self.method, time.time())).encode("utf-8"))
        return m.hexdigest()


class JsonRPCClient(object):

    def __init__(self, url, timeout=30, method_class=JsonRPCMethod):
        self.url = url
        self.timeout = timeout
        self.method_class = method_class

    def __getattr__(self, method):
        return self.method_class(self.url, method, timeout=self.timeout)


def jsonrpc_wrap(server, timeout):
    ERROR_CODE_BASE = -32000

    def _JsonRPCMethod(url, method, timeout, restart=True):
        _method_obj = JsonRPCMethod(url, method, timeout)

        def wrapper(*args, **kwargs):
            URLError = urllib3.exceptions.HTTPError if os.name == "nt" else urllib2.URLError
            try:
                return _method_obj(*args, **kwargs)
            except (URLError, socket.error, HTTPException) as e:
                if restart:
                    server.stop()
                    server.start(timeout=30)
                    return _JsonRPCMethod(url, method, timeout, False)(*args, **kwargs)
                else:
                    raise
            except JsonRPCError as e:
                if e.code >= ERROR_CODE_BASE - 1:
                    server.stop()
                    server.start()
                    return _method_obj(*args, **kwargs)
                elif e.code == ERROR_CODE_BASE - 2 and server.handlers['on']:  # Not Found
                    try:
                        server.handlers['on'] = False
                        # any handler returns True will break the left handlers
                        any(handler(server.handlers.get('device', None)) for handler in server.handlers['handlers'])
                    finally:
                        server.handlers['on'] = True
                    return _method_obj(*args, **kwargs)
                raise
        return wrapper

    return JsonRPCClient(server.rpc_uri,
                         timeout=timeout,
                         method_class=_JsonRPCMethod)
