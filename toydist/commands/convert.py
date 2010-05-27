import os
import sys
import traceback

from optparse \
    import \
        Option

from toydist.core.utils import \
        pprint, find_package
from toydist.core.parse_utils import \
        comma_list_split
from toydist.core.package import \
        static_representation
from toydist.conv import \
        distutils_to_package_description
from toydist.core.pkg_objects import \
        PathOption

from toydist.commands.core import \
        Command
from toydist.commands.errors import \
        UsageException, ConvertionError

class SetupCannotRun(Exception):
    pass

# ====================================================
# Code to convert existing setup.py to toysetup.info
# ====================================================
LIVE_OBJECTS = {}
def _process_data_files(seq):
    ret = []
    for i in seq:
        # FIXME: use package_dir to translate package to path
        target = i[0].replace(".", os.path.sep)
        srcdir = i[1]
        ret.append({"files": i[3], "srcdir": srcdir, "target": target})
    return ret

# XXX: this is where the magic happens. This is highly dependent on the
# setup.py, whether it uses distutils, numpy.distutils, setuptools and whatnot.
def monkey_patch(type, filename):
    supported = ["distutils", "setuptools", "setuptools_numpy"]

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

    def get_data_files():
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
        cmdclass = kw.get("cmdclass", {})
        try:
            _build_py = cmdclass["build_py"]
        except KeyError:
            _build_py = old_build_py

        class build_py_recorder(_build_py):
            def run(self):
                # package_data: data files which correspond to packages (and
                # are installed)

                # setuptools has its own logic to deal with package_data, and
                # do so in build_py
                if type == "setuptools":
                    LIVE_OBJECTS["package_data"] = _process_data_files(self._get_data_files())
                else:
                    LIVE_OBJECTS["package_data"] = []
                _build_py.run(self)

                # extra_data
                LIVE_OBJECTS["extra_data"] = get_data_files()

                LIVE_OBJECTS["data_files"] = self.distribution.data_files

        cmdclass["build_py"] = build_py_recorder
        kw["cmdclass"] = cmdclass

        dist = old_setup(**kw)
        LIVE_OBJECTS["dist"] = dist
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
    else:
        raise UsageException("Unknown converter: %s (known converters are %s)" % 
                         (type, ", ".join(supported)))

class ConvertCommand(Command):
    long_descr = """\
Purpose: convert a setup.py to an .info file
Usage:   toymaker convert [OPTIONS] setup.py"""
    short_descr = "convert distutils/setuptools project to toydist."
    opts = Command.opts + [
        Option("-t", help="TODO", default="automatic",
               dest="type"),
        Option("-o", "--output", help="output file",
               default="toysetup.info",
               dest="output_filename"),
        Option("-v", "--verbose", help="verbose run",
               action="store_true"),
        Option("--setup-arguments",
               help="arguments to give to setup" \
                    "For example, --setup-arguments=-q,-n,--with-speedup will " \
                    "call python setup.py -q -n --with-speedup",
               dest="setup_args")]

    def run(self, opts):
        self.set_option_parser()
        o, a = self.parser.parse_args(opts)
        if o.help:
            self.parser.print_help()
            return

        if len(a) < 1:
            filename = "setup.py"
        else:
            filename = a[0]
        if not os.path.exists(filename):
            raise ValueError("file %s not found" % filename)

        output = o.output_filename
        if os.path.exists(output):
            raise UsageException("file %s exists, not overwritten" % output)

        if o.verbose:
            show_output = True
        else:
            show_output = False

        if o.setup_args:
            setup_args = comma_list_split(o.setup_args)
        else:
            setup_args = ["-q", "-n"]

        tp = o.type

        convert_log = "convert.log"
        log = open(convert_log, "w")
        try:
            try:
                if tp == "automatic":
                    try:
                        pprint("PINK",
                               "Catching monkey (this may take a while) ...")
                        tp = detect_monkeys(filename, show_output, log)
                        pprint("PINK", "Detected mode: %s" % tp)
                    except ValueError, e:
                        raise UsageException("Error while detecting setup.py type " \
                                             "(original error: %s)" % str(e))

                monkey_patch(tp, filename)
                # analyse_setup_py put results in LIVE_OBJECTS
                dist = analyse_setup_py(filename, setup_args)
                pkg, options = build_pkg(dist, LIVE_OBJECTS)

                out = static_representation(pkg, options)
                if output == '-':
                    for line in out.splitlines():
                        pprint("YELLOW", line)
                else:
                    fid = open(output, "w")
                    try:
                        fid.write(out)
                    finally:
                        fid.close()
            except Exception, e:
                log.write("Error while converting - traceback:\n")
                tb = sys.exc_info()[2]
                traceback.print_tb(tb, file=log)
                msg = "Error while converting %s - you may look at %s for " \
                      "details (Original exception: %s %s)" 
                raise ConvertionError(msg % (filename, convert_log, type(e), str(e)))
        finally:
            log.flush()
            log.close()

def analyse_setup_py(filename, setup_args):
    pprint('PINK', "======================================================")
    pprint('PINK', " Analysing %s (running %s) .... " % (filename, filename))

    # exec_globals contains the globals used to execute the setup.py
    exec_globals = {}
    exec_globals.update(globals())
    # Some setup.py files call setup from their main, so execute them as if
    # they were the main script
    exec_globals["__name__"] = "__main__"
    exec_globals["__file__"] = os.path.abspath(filename)

    _saved_argv = sys.argv[:]
    _saved_sys_path = sys.path
    try:
        try:
            sys.argv = [filename] + setup_args + ["build_py"]
            # XXX: many packages import themselves to get version at build
            # time, and setuptools screw this up by inserting stuff first. Is
            # there a better way ?
            sys.path.insert(0, os.path.dirname(filename))
            execfile(filename, exec_globals)
            if type == "distutils" and "setuptools" in sys.modules:
                pprint("YELLOW", "Setuptools detected in distutils mode !!!")
        except Exception, e:
            pprint('RED', "Got exception: %s" % e)
            raise e
    finally:
        sys.argv = _saved_argv
        sys.path = _saved_sys_path

    if not "dist" in LIVE_OBJECTS:
        raise ValueError("setup monkey-patching failed")
    dist = LIVE_OBJECTS["dist"]

    pprint('PINK', " %s analyse done " % filename)
    pprint('PINK', "======================================================")

    return dist

def build_pkg(dist, live_objects):
    pkg = distutils_to_package_description(dist)
    path_options = []
    pkg.data_files = {}
    if live_objects["package_data"]:
        gendatafiles = {}
        for d in live_objects["package_data"]:
            if len(d["files"]) > 0:
                name = "gendata_%s" % d["target"].replace(os.path.sep, "_")
                gendatafiles[name] = {
                    "srcdir": canonalize_path(d["srcdir"]),
                    "target": posjoin("$gendatadir", canonalize_path(d["target"])),
                    "files": [canonalize_path(f) for f in d["files"]]
                }
        path_options.append(PathOption("gendatadir", "$sitedir",
                "Directory for datafiles obtained from distutils conversion"
                ))
        pkg.data_files.update(gendatafiles)

    extra_source_files = []
    if live_objects["extra_data"]:
        extra_source_files.extend([canonalize_path(f) 
                                  for f in live_objects["extra_data"]])
    pkg.extra_source_files = sorted(prune_extra_files(extra_source_files, pkg))

    if live_objects["data_files"]:
        pkg.data_files = combine_groups(live_objects["data_files"])

    # numpy.distutils bug: packages are appended twice to the Distribution
    # instance, so we prune the list here
    pkg.packages = sorted(list(set(pkg.packages)))
    options = {"path_options": path_options}
    
    return pkg, options

def prune_extra_files(files, pkg):

    package_files = []
    for p in pkg.packages:
        package_files.extend(find_package(p))

    data_files = []
    for sec in pkg.data_files:
        srcdir_field = pkg.data_files[sec]['srcdir']
        files_field = pkg.data_files[sec]['files']
        data_files.extend([os.path.join(srcdir_field, f) for f in files_field])

    redundant = package_files + data_files + pkg.py_modules

    return prune_file_list(files, redundant)

def detect_monkeys(setup_py, show_output, log):
    from toydist.commands.convert_utils import \
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
from ntpath import \
    join as ntjoin, split as ntsplit
from posixpath import \
    join as posjoin, normpath as posnormpath

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
    for e in data_files:
        # FIXME: install policies should not be handled here
        # FIXME: find the cases when entries' length are 2 vs 3
        if len(e) == 2:
            target = posjoin("$sitedir", e[0])
            sources = e[1]
        elif len(e) == 3:
            target = posjoin("$prefix", e[1])
            sources = e[2]

        for s in sources:
            srcdir = os.path.dirname(s)
            name = canonalize_path(os.path.basename(s))

            # Generate a unique key for target/source combination
            key = "%s_%s" % (target.replace(os.path.sep, "_"), srcdir.replace(os.path.sep, "_"))
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
    files_set = set([posnormpath(f) for f in files])
    redundant_set = set([posnormpath(f) for f in redundant])

    return list(files_set.difference(redundant_set))

def canonalize_path(path):
    """Convert a win32 path to unix path."""
    if os.path.sep == "/":
        return path
    head, tail = ntsplit(path)
    lst = [tail]
    while head and tail:
        head, tail = ntsplit(head)
        lst.insert(0, tail)
    lst.insert(0, head)

    return posjoin(*lst)
