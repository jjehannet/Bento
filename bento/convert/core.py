import os
import sys
import warnings
import posixpath
import ntpath

import os.path as op

from bento.compat.api \
    import \
        relpath
from bento.conv \
    import \
        find_package, distutils_to_package_description
from bento.core.utils \
    import \
        pprint, extract_exception
from bento.core.pkg_objects \
    import \
        PathOption, DataFiles

from bento.commands.errors \
    import \
        UsageException
from bento.convert.errors \
    import \
        ConvertionError

# ====================================================
# Code to convert existing setup.py to bento.info
# ====================================================
DIST_GLOBAL = None
PACKAGE_OBJECTS = None

class _PackageObjects(object):
    def __init__(self):
        self.package_data = {}
        self.extra_source_files = {}
        self.dist_data_files = []
        self.data_files = []

        self.build_lib = None

    def iter_data_files(self):
        for pkg_name, source_dir, build_dir, files in self.data_files:
            if files:
                target_dir = relpath(build_dir, self.build_lib)
                yield pkg_name, source_dir, op.join("$sitedir", target_dir), files
        yield "", ".", "$sitedir", self.dist_data_files

# XXX: this is where the magic happens. This is highly dependent on the
# setup.py, whether it uses distutils, numpy.distutils, setuptools and whatnot.
def monkey_patch(top_node, type, filename):
    supported = ["distutils", "numpy_distutils", "setuptools", "setuptools_numpy"]

    if type == "distutils":
        from distutils.core import setup as old_setup
        from distutils.command.build_py import build_py as old_build_py
        from distutils.command.sdist import sdist as old_sdist
        from distutils.dist import Distribution as _Distribution
        from distutils.filelist import FileList
    elif type == "setuptools":
        from setuptools import setup as old_setup
        from setuptools.command.build_py import build_py as old_build_py
        from setuptools.command.sdist import sdist as old_sdist
        from distutils.dist import Distribution as _Distribution
        from distutils.filelist import FileList
    elif type == "numpy_distutils":
        import numpy.distutils
        import distutils.core
        from numpy.distutils.core import setup as old_setup
        from numpy.distutils.command.build_py import build_py as old_build_py
        from numpy.distutils.command.sdist import sdist as old_sdist
        from numpy.distutils.numpy_distribution import NumpyDistribution as _Distribution
        from distutils.filelist import FileList
    elif type == "setuptools_numpy":
        import setuptools
        import numpy.distutils
        import distutils.core
        from numpy.distutils.core import setup as old_setup
        from numpy.distutils.command.build_py import build_py as old_build_py
        from numpy.distutils.command.sdist import sdist as old_sdist
        from numpy.distutils.numpy_distribution import NumpyDistribution as _Distribution
        from distutils.filelist import FileList
    else:
        raise UsageException("Unknown converter: %s (known converters are %s)" % 
                         (type, ", ".join(supported)))

    def get_extra_source_files():
        """Return the list of files included in the tarball."""
        # FIXME: handle redundancies between data files, package data files
        # (i.e. installed data files) and files included as part of modules,
        # packages, extensions, .... Given the giant mess that distutils makes
        # of things here, it may not be possible to get everything right,
        # though.
        dist = _Distribution()
        sdist = old_sdist(dist)
        sdist.initialize_options()
        sdist.finalize_options()
        sdist.manifest_only = True
        sdist.filelist = FileList()
        sdist.distribution.script_name = filename
        sdist.get_file_list()
        return sdist.filelist.files


    def new_setup(**kw):
        global DIST_GLOBAL, PACKAGE_OBJECTS
        package_objects = _PackageObjects()

        package_dir = kw.get("package_dir", None)
        if package_dir:
            keys = list(package_dir.keys())
            if len(keys) > 1:
                raise ConvertionError("setup call with package_dir=%r argument is not supported !" \
                                      % package_dir)
            elif len(keys) == 1:
                if package_dir.values()[0] != '':
                    raise ConvertionError("setup call with package_dir=%r argument is not supported !" \
                                          % package_dir)

        cmdclass = kw.get("cmdclass", {})
        try:
            _build_py = cmdclass["build_py"]
        except KeyError:
            _build_py = old_build_py

        class build_py_recorder(_build_py):
            def run(self):
                _build_py.run(self)

                package_objects.build_lib = self.build_lib
                package_objects.extra_source_files = get_extra_source_files()

                # This is simply the data_files argument passed to setup
                if self.distribution.data_files is not None:
                    package_objects.dist_data_files.extend(self.distribution.data_files)
                # those are created from package_data stuff (the stuff included
                # if include_package_data=True as well)
                package_objects.data_files = self.data_files

        cmdclass["build_py"] = build_py_recorder
        kw["cmdclass"] = cmdclass

        dist = old_setup(**kw)
        DIST_GLOBAL = dist
        PACKAGE_OBJECTS = package_objects
        return dist

    if type == "distutils":
        import distutils.core
        distutils.core.setup = new_setup
    elif type == "setuptools":
        import distutils.core
        import setuptools
        distutils.core.setup = new_setup
        setuptools.setup = new_setup
    elif type == "setuptools_numpy":
        numpy.distutils.core.setup = new_setup
        setuptools.setup = new_setup
        distutils.core.setup = new_setup
    elif type == "numpy_distutils":
        numpy.distutils.core.setup = new_setup
        distutils.core.setup = new_setup
    else:
        raise UsageException("Unknown converter: %s (known converters are %s)" % 
                         (type, ", ".join(supported)))

def analyse_setup_py(filename, setup_args, verbose=False):
    # This is the dirty part: we run setup.py inside this process, and pass
    # data back through global variables. Not sure if there is a better way to
    # do this
    if verbose:
        pprint('PINK', "======================================================")
        pprint('PINK', " Analysing %s (running %s) .... " % (filename, filename))

    # exec_globals contains the globals used to execute the setup.py
    exec_globals = {}
    exec_globals.update(globals())
    # Some setup.py files call setup from their main, so execute them as if
    # they were the main script
    exec_globals["__name__"] = "__main__"
    exec_globals["__file__"] = op.abspath(filename)

    _saved_argv = sys.argv[:]
    _saved_sys_path = sys.path
    try:
        try:
            sys.argv = [filename] + setup_args + ["build_py"]
            # XXX: many packages import themselves to get version at build
            # time, and setuptools screw this up by inserting stuff first. Is
            # there a better way ?
            sys.path.insert(0, op.dirname(filename))
            execfile(filename, exec_globals)
            if type == "distutils" and "setuptools" in sys.modules and verbose:
                pprint("YELLOW", "Setuptools detected in distutils mode !!!")
        except ConvertionError:
            raise
        except Exception:
            e = extract_exception()
            pprint('RED', "Got exception: %s" % e)
            raise
    finally:
        sys.argv = _saved_argv
        sys.path = _saved_sys_path

    live_objects = PACKAGE_OBJECTS
    dist = DIST_GLOBAL
    if dist is None:
        raise ValueError("setup monkey-patching failed")
    else:
        if verbose:
            pprint('PINK', " %s analyse done " % filename)
            pprint('PINK', "======================================================")
        return dist, live_objects

def build_pkg(dist, package_objects, top_node):
    pkg = distutils_to_package_description(dist)
    modules = []
    for m in pkg.py_modules:
        if isinstance(m, basestring):
            modules.append(m)
        else:
            warnings.warn("The module %s it not understood" % str(m))
    pkg.py_modules = modules

    path_options = []
    data_sections = {}

    extra_source_files = []
    if package_objects.extra_source_files:
        extra_source_files.extend([canonalize_path(f) 
                                  for f in package_objects.extra_source_files])
    pkg.extra_source_files = sorted(prune_extra_files(extra_source_files, pkg, top_node))

    for pkg_name, source_dir, target_dir, files in package_objects.iter_data_files():
        if len(files) > 0:
            if len(pkg_name) > 0:
                name = "%s_data" % pkg_name.replace(".", "_")
            else:
                name = "dist_data"
            data_sections[name] = DataFiles(name, files, target_dir, source_dir)
    pkg.data_files.update(data_sections)

    if dist.scripts:
        name = "%s_scripts" % pkg.name
        target_dir = "$eprefix"
        pkg.data_files[name] = DataFiles(name, dist.scripts, target_dir, ".")

    # numpy.distutils bug: packages are appended twice to the Distribution
    # instance, so we prune the list here
    pkg.packages = sorted(list(set(pkg.packages)))
    options = {"path_options": path_options}
    
    return pkg, options

def prune_extra_files(files, pkg, top_node):

    package_files = []
    for p in pkg.packages:
        package_files.extend(find_package(p, top_node))

    data_files = []
    for data_section in pkg.data_files.values():
        data_files.extend([op.join(data_section.source_dir, f) for f in data_section.files])

    redundant = package_files + data_files + pkg.py_modules

    return prune_file_list(files, redundant)

def detect_monkeys(setup_py, show_output, log):
    from bento.convert.utils import \
        test_distutils, test_setuptools, test_numpy, test_setuptools_numpy, \
        test_can_run

    if not test_can_run(setup_py, show_output, log):
        raise SetupCannotRun()

    def print_delim(string):
        if show_output:
            pprint("YELLOW", string)

    print_delim("----------------- Testing distutils ------------------")
    use_distutils = test_distutils(setup_py, show_output, log)
    print_delim("----------------- Testing setuptools -----------------")
    use_setuptools = test_setuptools(setup_py, show_output, log)
    print_delim("------------ Testing numpy.distutils -----------------")
    use_numpy = test_numpy(setup_py, show_output, log)
    print_delim("--- Testing numpy.distutils patched by setuptools ----")
    use_setuptools_numpy = test_setuptools_numpy(setup_py, show_output, log)
    print_delim("Is distutils ? %s" % use_distutils)
    print_delim("Is setuptools ? %s" % use_setuptools)
    print_delim("Is numpy distutils ? %s" % use_numpy)
    print_delim("Is setuptools numpy ? %s" % use_setuptools_numpy)

    if use_distutils and not (use_setuptools or use_numpy or use_setuptools_numpy):
        return "distutils"
    elif use_setuptools  and not (use_numpy or use_setuptools_numpy):
        return "setuptools"
    elif use_numpy  and not use_setuptools_numpy:
        return "numpy"
    elif use_setuptools_numpy:
        return "setuptools_numpy"
    else:
        raise ValueError("Unsupported converter")

# Functions below should always produce posix-style paths, even on windows
def combine_groups(data_files):
    """Given a list of tuple (target, files), combine files together per
    target/srcdir.
    
    Example
    -------
    
    data_files = [('foo', ['src/file1', 'src/file2'])]
    
    combine_groups returns:
        {'foo_src': {
            'target': 'foo',
            'srcdir': 'src',
            'files':
                ['file1', 'file2']}
        }.
    """

    ret = {}
    for data_file in data_files:
        # FIXME: install policies should not be handled here
        # FIXME: find the cases when entries' length are 2 vs 3 vs 4
        if len(data_file) == 2:
            target = posixpath.join("$sitedir", data_file[0])
            sources = data_file[1]
        elif len(data_file) == 3:
            target = posixpath.join("$prefix", data_file[1])
            sources = data_file[2]
        else:
            raise NotImplementedError("data files with >3 components not handled yet")

        for source in sources:
            srcdir = op.dirname(source)
            name = canonalize_path(op.basename(source))

            # Generate a unique key for target/source combination
            key = "%s_%s" % (target.replace(op.sep, "_"), srcdir.replace(op.sep, "_"))
            if ret.has_key(key):
                if not (ret[key]["srcdir"] == srcdir and ret[key]["target"] == target):
                    raise ValueError("BUG: mismatch for key %s ?" % key)
                ret[key]["files"].append(name)
            else:
                d = {}
                d["srcdir"] = srcdir
                d["target"] = target
                d["files"] = [name]
                ret[key] = d

    return ret

def prune_file_list(files, redundant):
    """Prune a list of files relatively to a second list.

    Return a subsequence of `files' which contains only files not in
    `redundant'

    Parameters
    ----------
    files: seq
        list of files to prune.
    redundant: seq
        list of candidate files to prune.
    """
    files_set = set([posixpath.normpath(f) for f in files if not isinstance(f, basestring)])
    redundant_set = set([posixpath.normpath(f) for f in redundant if not isinstance(f, basestring)])

    return list(files_set.difference(redundant_set))

def canonalize_path(path):
    """Convert a win32 path to unix path."""
    if op.sep == "/":
        return path
    head, tail = ntpath.split(path)
    lst = [tail]
    while head and tail:
        head, tail = ntpath.split(head)
        lst.insert(0, tail)
    lst.insert(0, head)

    return posixpath.join(*lst)
