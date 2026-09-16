#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdlib.h>

typedef struct {
    uint32_t status;
    uint8_t *headers;
    size_t headers_len;
    uint8_t *body;
    size_t body_len;
} gs_response;

typedef int (*dispatch_fn)(uint8_t*, size_t, uint8_t*, size_t, uint8_t*, size_t, uint8_t*, size_t, gs_response*);
typedef void (*free_fn)(gs_response*);
typedef uint32_t (*version_fn)(void);

static void *handle = NULL;
static dispatch_fn gs_dispatch_p = NULL;
static free_fn gs_free_response_p = NULL;
static version_fn gs_abi_version_p = NULL;

static PyObject *native_open(PyObject *self, PyObject *args) {
    const char *path;
    if (!PyArg_ParseTuple(args, "s", &path)) return NULL;
    if (handle) Py_RETURN_NONE;
    handle = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    if (!handle) return PyErr_Format(PyExc_OSError, "dlopen: %s", dlerror());
    gs_dispatch_p = (dispatch_fn)dlsym(handle, "gs_dispatch");
    gs_free_response_p = (free_fn)dlsym(handle, "gs_free_response");
    gs_abi_version_p = (version_fn)dlsym(handle, "gs_abi_version");
    if (!gs_dispatch_p || !gs_free_response_p || !gs_abi_version_p) {
        return PyErr_Format(PyExc_ImportError, "gospace ABI symbols missing");
    }
    Py_RETURN_NONE;
}

static PyObject *native_version(PyObject *self, PyObject *unused) {
    if (!gs_abi_version_p) return PyErr_Format(PyExc_RuntimeError, "gospace is not open");
    return PyLong_FromUnsignedLong(gs_abi_version_p());
}

static PyObject *native_dispatch(PyObject *self, PyObject *args) {
    const char *method, *uri, *headers, *body;
    Py_ssize_t method_len, uri_len, headers_len, body_len;
    if (!gs_dispatch_p) return PyErr_Format(PyExc_RuntimeError, "gospace is not open");
    if (!PyArg_ParseTuple(args, "y#y#y#y#", &method, &method_len, &uri, &uri_len, &headers, &headers_len, &body, &body_len)) return NULL;

    gs_response out = {0};
    int rc;
    Py_BEGIN_ALLOW_THREADS
    rc = gs_dispatch_p((uint8_t*)method, (size_t)method_len, (uint8_t*)uri, (size_t)uri_len, (uint8_t*)headers, (size_t)headers_len, (uint8_t*)body, (size_t)body_len, &out);
    Py_END_ALLOW_THREADS
    if (rc != 0) return PyErr_Format(PyExc_RuntimeError, "gospace dispatch failed: %d", rc);

    PyObject *py_headers = PyBytes_FromStringAndSize((const char*)out.headers, (Py_ssize_t)out.headers_len);
    PyObject *py_body = PyBytes_FromStringAndSize((const char*)out.body, (Py_ssize_t)out.body_len);
    uint32_t status = out.status;
    gs_free_response_p(&out);
    if (!py_headers || !py_body) { Py_XDECREF(py_headers); Py_XDECREF(py_body); return NULL; }
    PyObject *result = Py_BuildValue("IOO", status, py_headers, py_body);
    Py_DECREF(py_headers); Py_DECREF(py_body);
    return result;
}

static PyMethodDef methods[] = {
    {"open", native_open, METH_VARARGS, "Load libgospace."},
    {"abi_version", native_version, METH_NOARGS, "Return ABI version."},
    {"dispatch", native_dispatch, METH_VARARGS, "Dispatch one request."},
    {NULL, NULL, 0, NULL}
};
static struct PyModuleDef module = {PyModuleDef_HEAD_INIT, "_gospace_native", NULL, -1, methods};
PyMODINIT_FUNC PyInit__gospace_native(void) { return PyModule_Create(&module); }
