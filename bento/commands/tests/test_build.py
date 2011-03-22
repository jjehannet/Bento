import os
import shutil
import unittest
import tempfile

from bento.core \
    import \
        PackageDescription
import bento.commands.build_distutils
import bento.commands.build_yaku

from yaku.context \
    import \
        get_bld, get_cfg

BENTO_INFO = """\
Name: foo

Library:
    Extension: foo
        Sources: foo.c
"""

FOO_C = r"""\
#include <Python.h>
#include <stdio.h>

static PyObject*
hello(PyObject *self, PyObject *args)
{
    printf("Hello from C\n");
    Py_INCREF(Py_None);
    return Py_None;
}

static PyMethodDef HelloMethods[] = {
    {"hello",  hello, METH_VARARGS, "Print a hello world."},
    {NULL, NULL, 0, NULL}        /* Sentinel */
};

PyMODINIT_FUNC
init_bar(void)
{
    (void) Py_InitModule("_bar", HelloMethods);
}
"""

class _TestBuildSimpleExtension(unittest.TestCase):
    def setUp(self):
        self.save = None
        self.d = None

        self.save = os.getcwd()
        self.d = tempfile.mkdtemp()
        os.chdir(self.d)

    def tearDown(self):
        if self.save:
            os.chdir(self.save)
        if self.d:
            shutil.rmtree(self.d)

    def _test_simple_extension(self):
        for f, content in [("bento.info", BENTO_INFO), ("foo.c", FOO_C)]:
            fid = open(os.path.join(self.d, f), "w")
            try:
                fid.write(content)
            finally:
                fid.close()
        return PackageDescription.from_string(BENTO_INFO)

class TestBuildDistutils(_TestBuildSimpleExtension):
    def test_simple_extension(self):
        pkg = self._test_simple_extension()
        foo = bento.commands.build_distutils.build_extensions(pkg, use_numpy_distutils=False)
        isection = foo["foo"]
        self.assertTrue(os.path.exists(os.path.join(isection.source_dir, isection.files[0][0])))

    def test_simple_extension_with_numpy(self):
        pkg = self._test_simple_extension()
        foo = bento.commands.build_distutils.build_extensions(pkg, use_numpy_distutils=True)
        isection = foo["foo"]
        self.assertTrue(os.path.exists(os.path.join(isection.source_dir, isection.files[0][0])))

    def test_no_extension(self):
        fid = open(os.path.join(self.d, "bento.info"), "w")
        try:
            fid.write("Name: foo")
        finally:
            fid.close()
        pkg = PackageDescription.from_file("bento.info")
        foo = bento.commands.build_distutils.build_extensions(pkg)

class TestBuildYaku(_TestBuildSimpleExtension):
    def setUp(self):
        super(TestBuildYaku, self).setUp()

        ctx = get_cfg()
        ctx.use_tools(["ctasks", "pyext"])
        ctx.store()

        self.yaku_build = get_bld()

    def _build_extensions(self, pkg):
        return bento.commands.build_yaku.build_extensions(pkg.extensions,
            self.yaku_build, {}, {})

    def tearDown(self):
        try:
            self.yaku_build.store()
        finally:
            super(TestBuildYaku, self).tearDown()

    def test_simple_extension(self):
        pkg = self._test_simple_extension()

        foo = self._build_extensions(pkg)
        isection = foo["foo"]
        self.assertTrue(os.path.exists(os.path.join(isection.source_dir, isection.files[0][0])))

    def test_no_extension(self):
        fid = open(os.path.join(self.d, "bento.info"), "w")
        try:
            fid.write("Name: foo")
        finally:
            fid.close()
        pkg = PackageDescription.from_file("bento.info")
        self._build_extensions(pkg)
