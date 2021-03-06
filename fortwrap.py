#!/usr/bin/env python

# Copyright (c) 2010 John McFarland

# This program is licensed under the MIT license.  See LICENSE.txt

# Author: John McFarland

# This program will parse a selected set of Fortran source files and
# write C++ code to wrap the Fortran derived types in C++ classes

# Run fortwrap.py -h for usage information

from __future__ import print_function
# These imports were added by futurize.  In Python 2, the builtins
# module requires the "future" package.  These imports allow python 2
# to use the newer versions of the functions.  However, the code will
# still work in Python 2 without these, so we can ignore the import
# error.
try:
    from builtins import str
    from builtins import range
    from builtins import object
    # Not automatically added by future:
    from builtins import dict # If available, enable efficient iteration in Python 2.  Another option would be to use itervalues from six.
except ImportError:
    # Get here with a Python 2 that does not have the future package.
    # However, fortwrap will still work
    pass
import argparse
import re
import glob
from datetime import date
import sys
import os
import traceback


VERSION = '2.4.0'


# SETTINGS ==========================================

# Default Fortran compiler.  Can be overridden by command option
compiler = 'gfortran'

ERROR_FILE_NAME = 'FortWrap-error.txt'

code_output_dir = '.'
include_output_dir = '.'
fort_output_dir = '.'

HEADER_STRING = '/* This source file automatically generated on ' + str(date.today()) + ' using \n   FortWrap wrapper generator version ' + VERSION + ' */\n'

func_pointer_converter = 'convert_c_funcpointer'

misc_defs_filename = 'InterfaceDefs.h'
matrix_classname = 'FortranMatrix'
string_classname = 'FortranString'

orphan_classname = 'FortFuncs'
orphan_class_comments = ['Wrapper class for Fortran routines that do not operate on a derived type']
constants_classname = 'FortConstants'

fort_wrap_file = 'CppWrappers'
SWIG = True  # Whether or not to include preprocessor defs that will
             # be used by swig

# Macros to define DLLEXPORT, needed with MSC compiler
DLLEXPORT_MACRO = """#ifdef _MSC_VER
#define DLLEXPORT __declspec(dllexport)
#else
#define DLLEXPORT
#endif"""

# ===================================================


# REGULAR EXPRESSIONS ===============================

fort_type_def = re.compile(r'\s*TYPE(\s+(?P<name0>[a-z]\w*)|.*::\s*(?P<name1>[a-z]\w*))', re.IGNORECASE)
fort_type_extends_def = re.compile(r'EXTENDS\s*\(\s*([a-z]\w*)\s*\)', re.IGNORECASE)
fort_tbp_def = re.compile(r'\s*PROCEDURE\s*(\(\s*(?P<interface>[a-z]\w*)\))?.*::', re.IGNORECASE)

fort_proc_def = re.compile(r'\s*(RECURSIVE)?\s*(SUBROUTINE|FUNCTION)\s+\S+\(', re.IGNORECASE)
fort_end_proc = re.compile(r'\s*END\s*(SUBROUTINE|FUNCTION)', re.IGNORECASE)
fort_end_interface = re.compile(r'\s*END\s*INTERFACE', re.IGNORECASE)
fort_comment = re.compile('\s*!')
fort_contains = re.compile(r'\s*CONTAINS\s*$', re.IGNORECASE)

# Data types
primitive_data_str = '(INTEGER|REAL|DOUBLE PRECISION|LOGICAL|CHARACTER|INT|COMPLEX)(\s*(\*(?P<old_kind_spec>[0-9]+)|\(\s*((KIND|len)\s*=)?\s*(?P<kind_spec>(\w+|\*))\s*\)))?'
primitive_data = re.compile(primitive_data_str,re.IGNORECASE)
fort_data_str = r'\s*(' + primitive_data_str + '|(?P<dt_mode>TYPE|CLASS)\s*\((?P<dt_spec>\S*)\)|PROCEDURE\s*\((?P<proc_spec>\S*)\))'
fort_data = re.compile(fort_data_str,re.IGNORECASE)
fort_data_def = re.compile(fort_data_str + '\s*(?P<attributes>,.*)?::',re.IGNORECASE)
optional_def = re.compile('OPTIONAL', re.IGNORECASE)
byval_def = re.compile('VALUE', re.IGNORECASE)
allocatable_def = re.compile('ALLOCATABLE',re.IGNORECASE)
fort_pointer_def = re.compile('POINTER',re.IGNORECASE)
# CLASS: not yet supported, but print warnings
fort_class_data_def = re.compile(r'\s*CLASS\s*\(\S*\).*::',re.IGNORECASE)

module_def = re.compile(r'\s*MODULE\s+\S',re.IGNORECASE)
end_module_def = re.compile(r'\s*END\s+MODULE',re.IGNORECASE)
# INT below is used to represent the hidden length arguments, passed by value
fort_dox_comments = re.compile(r'\s*!\>')
fort_dox_inline = re.compile(r'\s*\w+.*!\<')
result_name_def = re.compile(r'.*RESULT\s*\(\s*(\w+)\s*\)',re.IGNORECASE)
intent_in_def = re.compile(r'.*INTENT\s?\(\s*IN\s*\)',re.IGNORECASE)
intent_out_def = re.compile(r'.*INTENT\s?\(\s*OUT\s*\)',re.IGNORECASE)
fort_abstract_def = re.compile(r'\s*ABSTRACT\s+INTERFACE',re.IGNORECASE)
integer_param_def = re.compile(r'\s+INTEGER,\s+PARAMETER\s+::',re.IGNORECASE)
# Regular expression to break up arguments.  This is needed to handle
# multi-dimensional arrays (e.g. A(m,n)).  It uses a negative
# lookahead assertion to exclude commas inside the array definition
arg_splitter = re.compile(',(?![^(]*\))')
dimension_def = re.compile(r'DIMENSION\s*\(\s*([^(]+)\s*\)',re.IGNORECASE)
enum_def = re.compile(r'\s*ENUM,\s*BIND',re.IGNORECASE)

# Constructor and desctructor methods (these regexes are configurable):
ctor_def = re.compile('.*_ctor', re.IGNORECASE)
dtor_def = re.compile('.*_dtor', re.IGNORECASE)
# Regular expression for "initialization" functions (configurable):
init_func_def = None

# ===================================================


# GLOBAL VARIABLES ==================================

objects = dict() # lower case object name -> DerivedType instance
procedures = []
abstract_interfaces = dict()
name_substitutions = dict()
pattern_substitutions = [] # List of (regex,replace) tuples
name_exclusions = set()
name_inclusions = set()
proc_arg_exclusions = set()
nopass_tbps = dict() # (module.lower,procname.lower) -> TypeBoundProcedure, for all TBP's with NOPASS attribute

# 'INT' is for the hidden name length argument
cpp_type_map = {'INTEGER':{'':'int*','1':'signed char*','2':'short*','4':'int*','8':'long long*','C_INT':'int*', 'C_LONG':'long*'}, 
                'REAL':{'':'float*', '4':'float*', '8':'double*', 'C_DOUBLE':'double*', 'C_FLOAT':'float*'},
                'LOGICAL':{'':'int*', 'C_BOOL':'int*'}, 
                'CHARACTER':{'':'char*'},
                'C_PTR':{'':'void**'},
                'COMPLEX':{'':'std::complex<float>*','4':'std::complex<float>*','C_FLOAT_COMPLEX':'std::complex<float>*','8':'std::complex<double>*','C_DOUBLE_COMPLEX':'std::complex<double>*'},
                'INT':{'':'fortran_charlen_t'}}

current_module = ''
module_proc_num = 1 # For keeping track of the order that the
                    # procedures are defined in the Fortran code.  Not
                    # needed now that they are stored in a list, but
                    # keep in case want to use later to achieve a
                    # different ordering than is in the source
dox_comments = []

fort_integer_params = dict()
enumerations = []

PRIVATE=1
PUBLIC=0

# Indicate whether or not we will need procedure pointer wrapper code
proc_pointer_used = False
# Whether or not matrices are used
matrix_used = False
stringh_used = False            # Whether "string.h" is used (needed for strcpy)
fort_class_used = False
string_class_used = False # Whether string outputs are used, which involves wrapping using a string class (either std::string or the generated wrapper class)
complex_used = False

# Gets changed to string_classname if --no-std-string is set
string_object_type = 'std::string'

not_wrapped = [] # List of procedures that weren't wrapped

# ===================================================

class FWTypeException(Exception):
    """Raised when an unexpected type is enountered in the code generation
    phase"""
    def __init__(self,type):
        self.type = type
    def __str__(self):
        return self.type

def warning(msg):
    sys.stderr.write('Warning: ' + msg + '\n')

def error(msg):
    sys.stderr.write('Error: ' + msg + '\n')

class Array(object):
    two_d_warning_written = False
    multi_d_warning_written = False
    def __init__(self,spec):
        global matrix_used
        # spec is the part inside the parentheses
        self.assumed_shape = False
        self.size_var = ''
        self.d = spec.count(',') + 1
        if ':' in spec:
            self.assumed_shape = True
        if self.d == 1:
            self.size_var = spec.strip()
        elif self.d == 2 and not opts.no_fmat:
            matrix_used = True
            self.size_var = tuple([v.strip() for v in spec.split(',')])

        if self.d==2 and opts.no_fmat:
            if not Array.two_d_warning_written:
                warning("Wrapping 2-D arrays as pointers can lead to ambiguity if writing SWIG typemaps for pointers.  This can be avoided by not using --no-fmat")
                Array.two_d_warning_written = True
        if self.d>2:
            if not Array.multi_d_warning_written:
                warning("Wrapping higher-dimensional arrays as pointers can lead to ambiguity if writing SWIG typemaps for pointers.")
                Array.multi_d_warning_written = True

        # Properties queried by the wrapper generator:
        # vec=vector: 1-d array
        self.vec = self.d==1 and not self.assumed_shape
        self.matrix = self.d==2 and not self.assumed_shape
        self.fort_only = self.assumed_shape

class CharacterLength(object):
    """
    val may be either a number, or in the case of assumed length, a
    string containing the argument name, which is needed in the
    wrapper code
    """
    def __init__(self,val):
        self.val = val
        self.assumed = False
    def set_assumed(self, argname):
        self.val = argname
        self.assumed = True
    def as_string(self):
        if self.assumed:
            return self.val+'_len__'
        else:
            return str(self.val)

class DataType(object):
    def __init__(self,type,array=None,str_len=None,hidden=False):
        global stringh_used, complex_used
        self.type = type
        self.kind = ''
        self.array = array
        self.str_len = str_len
        self.proc_pointer = False
        self.proc = False # PROCEDURE without POINTER attribute
        self.dt = False      # False, 'TYPE', or 'CLASS'
        self.hidden = hidden # hidden name length arg
        # For array, name of argument used to pass the array length:
        self.is_array_size = False # integer defining an array size
        self.is_matrix_size = False
        if type=='CHARACTER':
            stringh_used = True # Really only needed for intent(in)
        elif type.upper()=='COMPLEX':
            complex_used = True

        primitive_data_match = primitive_data.match(type)
        if primitive_data_match:
            # This line must be outside the next two checks since they
            # won't catch the case of just 'real'
            self.type = primitive_data_match.group(1).upper()
            if primitive_data_match.group('old_kind_spec'):
                self.kind = primitive_data_match.group('old_kind_spec')
            elif primitive_data_match.group('kind_spec'):
                self.kind = primitive_data_match.group('kind_spec')

            if type.upper() == 'DOUBLE PRECISION':
                self.type = 'REAL'
                self.kind = '8'

            # Handle use of named parameter as kind:
            if self.kind.upper() in fort_integer_params:
                self.kind = str(fort_integer_params[self.kind.upper()])

            if not self.valid_primitive():
                warning(self.type+'('+self.kind+') not supported')

        if not primitive_data_match and type!='INT':
            # (INT is used to represent the hidden length arguments,
            # passed by value)
            if 'PROCEDURE' in type.upper():
                m = fort_data.match(type)
                self.type = m.group('proc_spec')
                # Outside of this constructor, the pointer attribute
                # is checked, in which case "proc" is reset to False
                # and "proc_pointer" is set to True:
                self.proc = True
            else:
                m = fort_data.match(type)
                if m.group('dt_mode'):
                    self.dt = m.group('dt_mode').upper()
                    self.type = m.group('dt_spec')
                    if self.type.upper() == 'C_PTR':
                        # Don't treat like derived type
                        self.dt = False
                else:
                    raise FWTypeException(type)

        # Properties queried by the wrapper generator:
        # vec=vector: 1-d array
        self.vec = self.array and self.array.vec
        self.matrix = self.array and self.array.matrix

    # Determine whether type is a valid primitive type.  Intended to
    # verify KIND specs.  Doesn't check string lengths.  Kind specs
    # should already be stripped from type string and stored in kind
    # variable
    def valid_primitive(self):
        type_map = cpp_type_map.get(self.type)
        return type_map and self.kind.upper() in type_map

class Argument(object):
    def __init__(self,name,pos,type=None,optional=False,intent='inout',byval=False,allocatable=False,pointer=False,comment=[]):
        global string_class_used
        self.name = name
        self.pos = pos # Zero-indexed
        self.type = type
        self.comment = comment
        self.optional = optional
        self.intent = intent
        self.byval = byval
        self.allocatable = allocatable
        self.pointer = pointer # (doesn't include procedure pointers)
        self.native = False # Will get defined in
                            # associate_procedures; identifies derived
                            # type arguments that will get passed in
                            # C++ using native classes
        self.exclude = False # Whether it is excluded via the ignores file
        self.cpp_optional = False  # Whether or not it is optional to C++

        # Whether or not the argument is inside an abstract procedrue
        # definition.  This is not set on initialization, but later
        # during parsing. (Added this attribute to get "const"
        # statements to show up in the func pointer defs, which don't
        # use pass_by_val)
        self.in_abstract = False
        # Whether it represents the "self" argument of a method call
        self.is_self_arg = False

        if type and type.type=='CHARACTER' and intent!='in':
                string_class_used = True

        if opts.document_all_params and not self.comment:
            # Add an empty comment, which will trigger a \param entry
            # to be included in the generated doxygen comments
            self.comment = ['']
                
    def set_type(self,type):
        self.type = type

    def pass_by_val(self):
        """Whether we can pass this argument by val in the C++ interface

        Don't count Fortran arguments having the VALUE attribute, since they are already passed by value"""
        return not self.byval and not self.optional and not self.type.dt and self.intent=='in' and not self.type.array and not self.type.type=='CHARACTER' and not self.in_abstract

    def fort_only(self):
        if self.type.hidden:
            return True
        elif self.type.is_array_size or self.type.is_matrix_size:
            return True
        if self.type.array and self.type.array.fort_only:
            return True
        elif self.exclude:
            return True
        elif self.type.dt:
            if self.type.array:
                return True
        elif self.type.proc_pointer:
            if not self.type.type in abstract_interfaces:
                return True
            elif not self.intent=='in':
                return True
        elif self.type.proc:
            # PROCEDURE (without POINTER attribute) does not seem to be compatible with current wrapping approach (with gfortran)
            return True    
        elif self.type.type=='CHARACTER':
            if not self.intent=='inout' and self.type.str_len and not self.type.array:
                return False
            else:
                return True
        if self.type.type in cpp_type_map and not self.type.valid_primitive():
            return True
        if self.byval and (self.optional or self.type.dt or self.type.type=='CHARACTER' or self.type.proc_pointer):
            return True
        if self.allocatable:
            return True
        if self.pointer and not self.type.proc_pointer:
            return True
        return False
    
    def cpp_const(self):
        """Whether or not it can be const in C++"""
        # matrix and array types excluded because we don't want
        # FortranMatrix<const double>.  Exclude pass_by_val for
        # aesthetic reasons (to keep const from being applied to
        # non-pointer arguments in method definitions)
        return self.intent=='in' and not self.byval and not self.pass_by_val() and not self.type.matrix

    def cpp_type(self, value=False, native=False):
        """
        Convert a Fortran type to a C++ type.
        """
        if self.type.dt:
            if native:
                return self.type.type + '*'
            else:
                return 'ADDRESS'
        elif self.type.valid_primitive():
            if self.cpp_const():
                prefix = 'const '
            else:
                prefix = ''
            string = prefix + cpp_type_map[self.type.type.upper()][self.type.kind]
            if value or self.byval:
                return string[:-1] # Strip "*"
            else:
                return string
        elif self.type.proc_pointer and self.type.type in abstract_interfaces:
            proc = abstract_interfaces[self.type.type]
            if proc.retval:
                string = proc.retval.cpp_type(value=True) + ' '
            else:
                string = 'void '
            string = string + '(*' + self.name + ')'
            string = string + '(' + c_arg_list(proc,bind=True) + ')'
            return string
        else:
            raise FWTypeException(self.type.type)


class Procedure(object):
    def __init__(self,name,args,method,retval=None,comment=None,nopass=False):
        self.name = name
        self.args = args # By name
        self.method = method # None, 't' (TYPE), 'c' (CLASS)
        self.retval = retval
        self.comment = comment
        self.nopass = nopass

        self.nargs = len(args)
        self.module = current_module
        self.num = module_proc_num
        self.tbp = None
        self.ctor = False
        self.dtor = False
        self.in_orphan_class = False # Set in associate_procedures
        if self.method and not nopass:
            if ctor_def.match(name):
                self.ctor = True
            elif dtor_def.match(name) and self.valid_dtor():
                self.dtor = True
        # Check for exclusion:
        for arg in args.values():
            if (name.lower(),arg.name.lower()) in proc_arg_exclusions:
                if arg.optional:
                    arg.exclude = True
                else:
                    warning("Argument exclusions only valid for optional arguments: " + name + ', ' + arg.name)
        # Make ordered argument list
        self.arglist = sorted(self.args.values(), key=lambda arg: arg.pos)
        # Check for arguments that can have default values of NULL in C++
        for arg in reversed(self.arglist):
            # (Assumes the dictionary iteration order is sorted by key)
            if arg.optional:
                arg.cpp_optional = True
            else:
                break
        self.add_hidden_strlen_args()
        # Check for integer arguments that define array sizes:
        for arg in self.args.values():
            if arg.type.type.upper()=='INTEGER' and arg.intent=='in':
                if not opts.no_vector and self.get_vec_size_parent(arg.name):
                    # The check on opts.no_vector prevents writing
                    # wrapper code that tries to extract the array
                    # size from a C array
                    arg.type.is_array_size = True
                elif not opts.no_fmat and self.get_matrix_size_parent(arg.name):
                    arg.type.is_matrix_size = True
            
    def has_args_past_pos(self,ipos,bind):
        """Query whether or not the argument list continues past pos,
        accounting for c++ optional args

        ipos - 0-indexed argument position"""
        has = False
        for i,arg in enumerate(self.arglist):
            if i > ipos:
                if not arg.fort_only():
                    has = True
                elif bind:
                    has = True
        return has

    def add_hidden_strlen_args(self):
        """Add Fortran-only string length arguments to end of argument list"""
        nargs = len(self.args)
        pos = nargs
        for arg in self.arglist:
            if arg.type.type=='CHARACTER' and not arg.fort_only():
                str_length_arg = Argument(arg.name + '_len__', pos, DataType('INT', str_len=arg.type.str_len ,hidden=True))
                self.args[ str_length_arg.name ] = str_length_arg
                self.arglist.append(str_length_arg)
                pos = pos + 1

    def get_vec_size_parent(self,argname):
        """Get the first 1d array argument that uses the argument argname to
        define its size"""
        for arg in self.arglist:
            if arg.type.vec and not arg.optional and arg.type.array.size_var==argname:
                return arg.name
        return None

    def get_matrix_size_parent(self,argname):
        """Similar to get_vec_size_parent.  Returns (matname,0) if argname
        is the number of rows and (matname,1) if argname is the number of
        columns"""
        for arg in self.arglist:
            if arg.type.matrix and not arg.optional and argname in arg.type.array.size_var:
                if argname == arg.type.array.size_var[0]:
                    return arg.name,0
                else:
                    return arg.name,1
        return None

    def has_post_call_wrapper_code(self):
        """Whether or not wrapper code will be needed for after the call to
        the Fortran procedure"""
        for arg in self.args.values():
            # Special string handling for after the call needed
            if arg.type.type=='CHARACTER' and not arg.fort_only() and arg.intent=='out':
                return True
        return False

    def valid_dtor(self):
        """Whether the procedure is a valid dtor.  Mainly this just
        checks that there are no required arguments"""
        for arg in self.args.values():
            if arg.pos > 0 and not arg.optional:
                return False
        return True
        
        
class DerivedType(object):
    def __init__(self,name,comment=None):
        self.name = name
        self.cname = translate_name(name) # Account for name renaming
        self.procs = []
        self.module = current_module
        self.comment = comment
        self.pointer_return_types = set() # Objects that methods of this type can return using pointers
        self.has_pointer_ctor = False

        self.is_class = False
        self.abstract = False
        self.extends = None
        self.tbps = dict() # lowercase procname => tbp instance

    def add_tbp(self, tbp):
        self.tbps[tbp.procname.lower()] = tbp

    def ctor_list(self):
        """Return list of associated ctors"""
        ctors = []
        for proc in self.procs:
            if proc.ctor:
                ctors.append(proc)
        return ctors

    def get_pointer_return_types(self):
        for proc in self.procs:
            if proc.retval and proc.retval.pointer and proc.retval.type.dt and proc.retval.type.type.lower() in objects and proc.retval.type.type.lower() != self.name.lower():
                self.pointer_return_types.add(proc.retval.type.type)

class TypeBoundProcedure(object):
    def __init__(self, name, procname, interface, deferred, nopass):
        global current_module, nopass_tbps
        self.name = name
        self.procname = procname
        # Initially False or a string, but the string gets changed to
        # a procedure instance in associate_procedures
        self.interface = interface
        self.deferred = deferred
        self.nopass = nopass
        if nopass:
            nopass_tbps[current_module.lower(), procname.lower()] = self

        
def mangle_name(mod,func):
    if mod:
        if compiler == 'g95':
            return mod.lower() + "_MP_" + func.lower()
        else: # gfortran
            return '__' + mod.lower() + '_MOD_' + func.lower()
    else:
        suffix = '_'
        if compiler=='g95' and '_' in func:        
            suffix = suffix + '_'
        return func.lower() + suffix

def vtab_symbol(mod,name):
    return '__{0}_MOD___vtab_{0}_{1}'.format(mod.lower(), name.capitalize())


def get_proc(name):
    """Return the first procedure in the global list that matches name"""
    for proc in procedures:
        if proc.name.lower()==name.lower():
            return proc
    return None

def remove_const(argtype):
    """Remove const from argtype"""
    if argtype.startswith('const'):
        return argtype.split('const ')[1]
    return argtype


def translate_name(name):
    """Use a substitution dictionary followed by a list of replacement patterns to translate the provided name, or return the input unchanged if no matches are found"""
    if name.lower() in name_substitutions:
        return name_substitutions[name.lower()]
    else:
        for pattern,replacement in pattern_substitutions:
            name = pattern.sub(replacement,name)
        return name


class FileReader:
    """Wrapper around file that provides facilities for backing up"""
    def __init__(self, fname):
        try:
            with open(fname, 'r') as f:
                self.file_lines_list = f.readlines()
        except IOError:
            error("Error opening file " + fname)
            self.file_lines_list = None
            self.success = False
        else:
            self.file_pointer = 0
            self.success = True

    # Note on reading and joining lines.  Did try automatically
    # calling join_lines inside readlines (with exception within
    # parse_comments), but that can fail for certain cases with string
    # continuations, which aren't handled correctly due to the second
    # "&".  A string continuation at the right place (e.g. right
    # before the end of a procedure) would cause the resulting parsing
    # to fail.
    def readline(self):
        """Acts like readline, but grabs the line out of a global list.  This
        way can move backwards in the list

        """
        if self.file_pointer < len(self.file_lines_list):
            self.file_pointer += 1
            return self.file_lines_list[self.file_pointer-1]
        else:
            return ''

    def join_lines(self, line):
        """Join lines possibly continued by & character"""
        while '&' in line.split('!')[0]:
            line1 = line.split('&')[0]
            line2 = self.readline()
            line = line1 + line2.strip()
        return line

    def parse_comments(self,line):
        """
        Parse doxygen comments.  On inline comments, return file back to
        line containing comment start.
        """
        #print "Parsing comment:", line
        com = []
        read_count = 0
        if fort_dox_comments.match(line):
            com.append(line.split('!>')[1].strip())
        elif fort_dox_inline.match(line):
            com.append(line.split('!<')[1].strip())
        else:
            warning("Bad line in parse_comments: " + line)
        while True:
            line = self.readline().strip()
            if line.startswith('!!'):
                text = line.split('!!')[1]
                # Preserve whitespace indentation:
                if text.startswith(' '):
                    text = text[1:]
                com.append(text)
                read_count+=1
            else:
                # Back up a line, or all the way back to the comment start
                # if it is inline
                if fort_dox_inline.match(line):
                        for i in range(read_count):
                            self.file_pointer -= 1
                self.file_pointer -= 1
                break
        return com    


def is_public(name):
    """Check whether a name is public and not excluded

    The private/public parts of this check rely on global variables
    that are updated while the Fortran source is processed, so this
    function should only be called during the parsing portion (not
    during code generation, after parsing is finished)

    """
    if name.lower() in name_exclusions:
        return False
    elif name.lower() in name_inclusions:
        return True
    elif default_protection == PUBLIC:
        return not name.lower() in private_names
    else:
        return name.lower() in public_names
        

# TODO: delete if not being used
def args_have_comment(args):
    """Whether or not there are any comments in a set of arguments"""
    for arg in args.values():
        if arg.comment:
            return True
    return False

def add_type(t):
    if is_public(t):
        objects[t.lower()] = DerivedType(t,dox_comments)
        return True
    else:
        return False

def parse_argument_defs(line,file,arg_list_lower,args,retval,comments):
    global proc_pointer_used
    
    count = 0

    line = file.join_lines(line)
    line = line.split('!')[0]

    m = fort_data_def.match(line)

    attributes = m.group('attributes')

    # Attributes.
    if not attributes:
        # Change None to string so searching works
        attributes = ''
    optional = True if optional_def.search(attributes) else False
    byval = True if byval_def.search(attributes) else False
    allocatable = True if allocatable_def.search(attributes) else False
    pointer = True if fort_pointer_def.search(attributes) else False
    # Check for DIMENSION statement
    dimension = dimension_def.search(attributes)
    intent = 'inout'
    if intent_in_def.match(attributes):
        intent = 'in'
    elif intent_out_def.match(attributes):
        intent = 'out'

    # Get type string [e.g. INTEGER, TYPE(ErrorType),
    # PROCEDURE(template), POINTER]
    type_string = m.group(1)

    # Process character length
    char_len = None
    if type_string.upper().startswith('CHARACTER'):
        type_string = 'CHARACTER'
        if m.group('old_kind_spec'):
            char_len = int( m.group('old_kind_spec') )
        elif m.group('kind_spec'):
            spec = m.group('kind_spec')
            if spec == '*': 
                char_len = '*'
            else:
                try:
                    char_len = int(spec)
                except ValueError:
                    # Check for string in Fortran parameter
                    # dictionary.  Note, this conversion/check could
                    # be done later when writing the wrapper code (so
                    # that all source has been parsed): in that case,
                    # make sure have proper checks so that it doesn't
                    # conflict with treatment of assumed length
                    # strings
                    char_len = fort_integer_params.get(spec.upper(), None)
        else:
            # No char length spec found.  Could use this to wrap
            # scalar CHARACTER
            char_len = None     # Redundant
        char_len = CharacterLength(char_len)

    # Get name(s)
    arg_string = line.split('::')[1].split('!')[0]
    names = [name.strip() for name in arg_splitter.split(arg_string)]
    for name in names:
        array = None
        if dimension:
            array = Array(dimension.group(1))
        elif '(' in name:
            size_desc = name.split('(')[1].split(')')[0]
            array = Array(size_desc)
            name = name.split('(')[0]
        type = DataType(type_string,array,char_len)
        if type.proc and pointer:
            type.proc = False
            type.proc_pointer = True
            proc_pointer_used = True
        if name.lower() in arg_list_lower:
            count += 1
            if char_len and char_len.val=='*':
                # Assign a new char_len instance and set it to contain
                # a reference to the argument name, which will be
                # needed in the wrapper code
                type.str_len = CharacterLength('*')
                type.str_len.set_assumed(name)
            args[name] = Argument(name,arg_list_lower.index(name.lower()),type,optional,intent,byval,allocatable,pointer,comment=comments)
        elif retval and name.lower()==retval.name.lower():
            retval.set_type(type)
            retval.pointer = pointer
            retval.comment = comments
    return count

def parse_proc(file,line,abstract=False):
    """
    Parse a procedure definition, given starting line
    """
    global dox_comments, abstract_interfaces, module_proc_num, not_wrapped
    # Store in case decide to overwrite them when parsing arg comments
    proc_comments = dox_comments 

    # First check for line continuation:
    line = file.join_lines(line)
    proc_name = line.split('(')[0].split()[-1]

    arg_string = line.split('(')[1].split(')')[0]
    if re.search(r'\S',arg_string):
        arg_list_lower = arg_string.split(',')
        arg_list_lower = [x.strip().lower() for x in arg_list_lower] # Remove whitespace and convert to lowercase
    else:
        arg_list_lower = []
    args = dict()
    #print arg_list_lower
    retval = None
    if re.match('^\s*function', line, re.IGNORECASE):
        m = result_name_def.match(line)
        if m:
            retval = Argument(m.group(1), None) # pos not used
        else:
            retval = Argument(proc_name, None) # pos not used
    # Determine argument types
    method = False
    invalid = False
    arg_comments = []
    while True:
        line = file.readline()
        if not line:
            error("Unexpected end of file parsing procedure '{}'".format(proc_name))
            return
        elif fort_end_proc.match(line):
            break
        elif fort_proc_def.match(line):
            # This means we found a contained procedure.  We need to
            # parse through it to the end to make sure that its
            # arguments don't overwrite the parent's arguments, and
            # that we don't wrap the contained procedure itself
            while True:
                line = file.readline()
                if not line:
                    error("Unexpected end of file parsing contained procedure within '{}'".format(proc_name))
                    return
                elif fort_end_proc.match(line):
                    break                
        elif fort_contains.match(line):
            in_contains = True
        elif fort_dox_comments.match(line):
            arg_comments = file.parse_comments(line)
            continue
        elif fort_dox_inline.match(line):
            # Note: don't CONTINUE here b/c still need to parse this arg
            arg_comments = file.parse_comments(line)
        elif fort_comment.match(line):
            continue
        if fort_data_def.match(line):
            # Could check below return value to see if in local
            # variables (assuming definitions are ordered in code)
            parse_argument_defs(line,file,arg_list_lower,args,retval,arg_comments)
            arg_comments = []
        elif fort_class_data_def.match(line):
            warning("CLASS arguments not currently supported: " + proc_name)
    # Check args:
    if len(args) != len(arg_list_lower):
        missing_args = set(arg_list_lower).difference(set(args.keys()))
        error("Missing argument definitions in procedure {}: {}".format(proc_name, ', '.join(missing_args)))
        invalid = True
    for arg in args.values():
        if arg.fort_only() and not arg.optional:
            # Note: the above fort_only() call depends on the
            # appropriate abstract interfaces already having been
            # defined.  Make sure the Fortran source file that
            # contains those definitions is parsed first
            invalid = True
        if arg.pos==0 and arg.type.dt:
            method = True
            arg.is_self_arg = True
    if retval:
        if retval.type:
            if retval.type.array:
                invalid = True
            elif retval.type.type == 'CHARACTER':
                invalid = True
            elif retval.type.dt:
                if not retval.pointer:
                    invalid = True
            elif not retval.type.valid_primitive():
                invalid = True
        else:
            error("Untyped return value in {}: {}".format(proc_name,retval.name))
            invalid = True
    if invalid:
        not_wrapped.append(proc_name)
    else:
        is_tbp = False
        nopass = False
        if not method:
            if (current_module.lower(), proc_name.lower()) in nopass_tbps:
                is_tbp = True
                method = True
                nopass = True
        proc = Procedure(proc_name,args,method,retval,proc_comments,nopass)
        if method and not nopass:
            try:
                if proc_name.lower() in objects[proc.arglist[0].type.type.lower()].tbps:
                    is_tbp = True
            except KeyError:
                # Should mean that the derived type is not public
                pass
        # Note: wrap all abstract procedures (even if they are not
        # public).  This is sort of a hack, as technically we should
        # only wrap non-public abstract procedures if they are used in
        # TBP definitions
        if is_public(proc_name) or is_tbp or abstract:
            if not abstract:
                procedures.append(proc)
                module_proc_num += 1
            else:
                abstract_interfaces[proc_name] = proc

def parse_abstract_interface(file,line):
    global dox_comments
    while True:
        line = file.readline()
        if line=='':
            print("Unexpected end of file in abstract interface")
            return
        elif fort_dox_comments.match(line):
            # Grab comments and ignore them
            dox_comments = file.parse_comments(line)
            continue
        elif fort_end_interface.match(line):
            break
        elif fort_comment.match(line):
            continue
        elif fort_proc_def.match(line):
            parse_proc(file,line,abstract=True)
            dox_comments = []

def parse_type(file,line):
    global fort_class_used
    m = fort_type_def.match(line)
    typename = m.group('name0') or m.group('name1')
    type_added = add_type(typename)
    # type_added checks below prevent KeyError's in objects[]
    if type_added and 'ABSTRACT' in line.upper():
        objects[typename.lower()].abstract = True
        objects[typename.lower()].is_class = True
        fort_class_used = True
    extends_match = fort_type_extends_def.search(line)
    if type_added and extends_match:
        objects[typename.lower()].extends = extends_match.group(1)
        objects[typename.lower()].is_class = True
        fort_class_used = True
    # if type_added:
    #     print "{} extends: {}".format(typename, objects[typename.lower()].extends)
    # Move to end of type and parse type bound procedure definitions
    while True:
        line = file.readline()
        line = file.join_lines(line)
        if line == '':
            error("Unexpected end of file in TYPE " + typename)
            return
        elif line.upper().strip().startswith('END TYPE'):
            return
        tbp_match = fort_tbp_def.match(line)
        if type_added and tbp_match:
            attr_str = line.split('::')[0].upper()
            if 'POINTER' in attr_str:
                continue # Not a TBP
            if 'NOPASS' in attr_str:
                nopass = True
            else:
                nopass = False
            interface = tbp_match.group('interface')
            deferred = 'DEFERRED' in attr_str
            for tbp_def in line.split('::')[1].split(','):
                name = tbp_def.split('=>')[0].strip()
                proc = tbp_def.split('=>')[1].strip() if '=>' in tbp_def else name
                tbp = TypeBoundProcedure(name, proc, interface, deferred, nopass)
                objects[typename.lower()].add_tbp(tbp)


def parse_enum(file,line):
    """
    Parse enum definition
    """
    enums = []
    while True:
        line = file.readline()
        if line == '':
            print("Unexpected end of file in ENUM")
            return
        if line.strip().upper().startswith('END'):
            break
        else:
            if line.strip().upper().startswith('ENUMERATOR'):
                line = file.join_lines(line)
                try:
                    s = line.split('::')[1].split('!')[0]
                    if s.find('=')>=0:
                        print("Non-default enum values not supported")
                        enums = []
                        break
                    for enum in s.split(','):
                        if is_public(enum.strip()):
                            enums.append(enum.strip())
                except:
                    print("Problem parsing ENUMERATOR:", line)
    if len(enums) > 0:
        #print "Adding enum:", enums
        enumerations.append(enums)
    

def initialize_protection():
    global default_protection, private_names, public_names
    default_protection = PUBLIC
    private_names = set()
    public_names = set()


def parse_file(fname):
    global current_module, dox_comments, default_protection, private_names, public_names
    f = FileReader(fname)
    if not f.success:
        return 0
    current_module = ''
    initialize_protection()
    dox_comments = []
    while True:
        line = f.readline()
        if line == '':
            break
        elif fort_dox_comments.match(line):
            dox_comments = f.parse_comments(line)
            continue
        elif fort_comment.match(line):
            continue
        elif module_def.match(line) and 'PROCEDURE' not in line.upper():
            current_module = line.split()[1]
            #print 'MOD:', current_module
            dox_comments = []
            initialize_protection()
            module_proc_num = 1
        elif end_module_def.match(line):
            current_module = ''
        elif fort_type_def.match(line):
            #print line.split()[1]
            parse_type(f,line)
            dox_comments = []
        elif fort_proc_def.match(line):
            #print line.split()[1].split('(')[0]
            parse_proc(f,line)
            dox_comments = []
        elif fort_abstract_def.match(line):
            parse_abstract_interface(f,line)
            dox_comments = []
        elif line.strip().upper().startswith('PRIVATE'):
            if '::' not in line:
                default_protection = PRIVATE
            else:
                line = f.join_lines(line)
                for name in line.split('::')[1].split(','):
                    private_names.add(name.strip().lower())
        elif line.strip().upper().startswith('PUBLIC'):
            if '::' in line:
                line = f.join_lines(line)
                for name in line.split('::')[1].split(','):
                    public_names.add(name.strip().lower())
        elif integer_param_def.match(line):
            line = f.join_lines(line).split('!')[0]
            for param in line.split('::')[1].split(','):
                name,val = param.split('=')
                fort_integer_params[name.strip().upper()] = int( val.strip() )
        elif enum_def.match(line):
            parse_enum(f,line)
    return 1

def associate_procedures():
    """
    After collecting the global objects and procedures list,
    associate procedures with objects, where applicable
    """
    global fort_class_used
    def flag_native_args(proc):
        # Check for arguments to pass as native classes:
        for ipos,arg in enumerate(proc.arglist):
            if ipos>0 and arg.type.dt and not arg.type.array and arg.type.type.lower() in objects:
                arg.native = True
        if proc.retval and proc.retval.pointer and proc.retval.type.dt and proc.retval.type.type.lower() in objects:
            proc.retval.native = True
            objects[proc.retval.type.type.lower()].has_pointer_ctor = True

    for proc in procedures:
        # Associate methods
        if proc.method:
            if not proc.nopass:
                typename = proc.arglist[0].type.type
                if typename.lower() in objects:
                    # print "Associating procedure:", typename +'.'+proc.name
                    objects[typename.lower()].procs.append(proc)
                    if proc.arglist[0].type.dt == 'CLASS' and proc.name.lower() in objects[typename.lower()].tbps:
                        proc.tbp = objects[typename.lower()].tbps[proc.name.lower()]
                    flag_native_args(proc)
                elif typename.lower() not in name_exclusions:
                    error("Method {} declared for unknown derived type {}".format(proc.name, typename))
            else: # nopass
                found = False
                for obj in objects.values():
                    # Only search for NOPASS procedures in the same
                    # module as the TYPE definition.  Otherwise, there
                    # is no way to ensure that the correct procedure
                    # gets associated if the name is reused in other
                    # modules.
                    if (obj.module==proc.module) and proc.name.lower() in obj.tbps:
                        objects[obj.name.lower()].procs.append(proc)
                        proc.tbp = obj.tbps[proc.name.lower()]
                        found = True
                        break
                if not found:
                    error("Unable to associate NOPASS type bound procedure {}".format(proc.name))
        # Associate orphan functions with a dummy class
        elif (not opts.no_orphans) or proc.name.lower() in name_inclusions:
            if not orphan_classname.lower() in objects:
                objects[orphan_classname.lower()] = DerivedType(orphan_classname,orphan_class_comments)
            objects[orphan_classname.lower()].procs.append(proc)
            flag_native_args(proc)
            proc.in_orphan_class = True
    for proc in abstract_interfaces.values():
        # Tag procedure arguments as being inside abstract interface
        for arg in proc.args.values():
            arg.in_abstract = True
        # Flag native args for abstrat interfacs, which is necessary when writing the prototypes for the virtual methods
        flag_native_args(proc)
    for obj in objects.values():
        obj.get_pointer_return_types()
    # Flag all derived types that are passed using CLASS:
    for proc in procedures:
        for arg in proc.args.values():
            if arg.type.dt == 'CLASS':
                typename = arg.type.type.lower()
                if typename in objects:
                    objects[typename].is_class = True
                    fort_class_used = True
            

def write_cpp_dox_comments(file, comments, arglist=None, retval=None, prefix=0):
    # /// Style
    #for c in comments:
    #    file.write(prefix*' ' + '/// ' + c + '\n')

    # Prefix written on each line before comments
    comment_prefix = prefix*' ' + ' * '
    
    class context:
        # Workaround for Python 2 nonlocal access in open_comments function
        started = False
    
    def open_comments():
        file.write(prefix*' ' + '/**\n')
        file.write(comment_prefix)
        context.started = True
        
    # First write primary symbol comments.  Capture any comments that
    # come after a "\par" command so that they can be written after
    # the \param comments.
    par_comments = []
    have_par_comments = False
    for i,c in enumerate(comments):
        if i == 0:
            open_comments()
            file.write('\\brief ' + c + '\n')
        else:
            if not have_par_comments and c.strip().startswith(r'\par') and len(c.strip().split()) > 1:
                have_par_comments = True
            if have_par_comments:
                par_comments.append(c)
            else:
                file.write(comment_prefix + c + '\n')
    # Write parameter argument comments if provided
    param_started = False
    if arglist:
        for arg in arglist:
            if arg.fort_only():
                continue
            if arg.is_self_arg:
                # This is important with opts.document_all_params
                continue
            for i,c in enumerate(arg.comment):
                if context.started:
                    if not param_started and not have_par_comments:
                        # Don't add line if have_par_comments b/c
                        # assume that we already pulled in a blank
                        # line just before the \par command
                        file.write(comment_prefix[:-1] + '\n')
                    file.write(comment_prefix)
                else:
                    open_comments()
                if i==0:
                    file.write('\\param')
                    if arg.intent=='in':
                        file.write('[in]')
                    elif arg.intent=='out':
                        file.write('[out]')
                    file.write(' ' + arg.name + ' ')
                file.write(c + '\n')
                param_started = True
    if retval and retval.comment:
        if context.started:
            if not param_started:
                file.write(comment_prefix[:-1] + '\n')
            file.write(comment_prefix)
        else:
            open_comments()
        file.write('\\return ')
        for c in retval.comment:
            file.write(c + '\n')
    # Now write the \par comments, so that they show after the \param
    # section
    if have_par_comments:
        file.write(comment_prefix[:-1] + '\n')
        for c in par_comments:
            file.write(comment_prefix + c + '\n')
    if context.started:
        # Close comments
        file.write(prefix*' ' + ' */\n')


def c_arg_list(proc,bind=False,call=False,definition=True):
    """
    Return the C argument list as a string.  definition: defined as
    opposed to declared (prototype)
    """
    # dt check is necessary to handle orphan functions in the dummy class
    if (not call) and (proc.nargs == 0 or (not bind and proc.nargs==1 and proc.arglist[0].type.dt)):
        return 'void'
    string = ''
    # Pass "data_ptr" as first arg, if necessary. dt check is necessary to
    # handle orphan functions in the dummy class correctly
    if bind and call and proc.nargs>0 and proc.arglist[0].type.dt:
        string += 'data_ptr' if proc.arglist[0].type.dt=='TYPE' else 'class_data_ptr'        
        if proc.nargs==1:
            return string
        else:
            string = string + ', '
    # Add argument names and possibly types
    for ipos,arg in enumerate(proc.arglist):
        if (call or not bind) and ipos == 0 and arg.type.dt:
            # dt check above excludes the cases where this is an
            # orphan function in the dummy class
            continue
        # Print warning for derived types that are not in the
        # interface (i.e. can't be passed "natively").  These don't
        # get caught by fort_only, b/c it isn't aware of the object
        # list (the native property isn't assigned until
        # associate_procedures is run).  Can always exclude these args
        # from the arg list or add the derived type to the interface
        if (not arg.fort_only() and not bind and not call and definition) and (arg.type.dt and not arg.native and arg.type.type.upper()!='C_PTR'):
            error("Derived type argument {}::{} of procedure {} is not defined".format(arg.type.type, arg.name, proc.name))
        if arg.fort_only():
            # Hide from user -- requires special treatement for three of
            # the four cases
            if bind and call:
                if arg.type.hidden:
                    string += arg.type.str_len.as_string()
                elif arg.type.is_array_size or arg.type.is_matrix_size:
                    string = string + '&' + arg.name
                else:
                    string = string + 'NULL'
                if proc.has_args_past_pos(ipos,bind):
                    string = string + ', '
                continue
            elif not bind:
                continue
        if not call:
            # Prepend type spec
            if arg.type:
                if not bind and arg.type.matrix and not opts.no_fmat:
                    # Special matrix handling
                    if arg.intent=='in':
                        # const has to be handled separately for this case
                        string = string + 'const '
                    string = string + matrix_classname + '<' + arg.cpp_type(value=True) + '> *'
                elif arg.type.proc_pointer and bind and not call:
                    # Fortran function pointer in C prototype:
                    string = string + 'void* '
                elif not bind and arg.native:
                    string = string + arg.type.type + '* '
                elif not bind and arg.pass_by_val():
                    string = string + arg.cpp_type(value=True) + ' '
                elif not arg.type.dt and arg.type.vec:
                    if not bind and not opts.no_vector:
                        if arg.intent=='in':
                            # const is manually removed inside <>, so
                            # add it before the type declaration
                            string = string + 'const '
                        string = string + 'std::vector<' + remove_const(arg.cpp_type(value=True)) + '>* '
                    else:
                        string = string + arg.cpp_type() + ' '
                        if not opts.array_as_ptr:
                            # Chop of the * and add [] after the argname below
                            string = string[:-2] + ' '
                elif arg.type.type=='CHARACTER' and not bind and not arg.intent=='in':
                    string = string + string_object_type + ' *' # pass by ref not compat with optional
                else:
                    string = string + arg.cpp_type() + ' '
            else:
                raise FWTypeException(arg.type.type)
        # Change pass-by-value to reference for Fortran
        if call and bind and arg.pass_by_val() and not arg.byval and not arg.type.proc_pointer:
            string = string + '&'
        # Add argument name -------------------------
        if arg.type.proc_pointer and not bind:
            # Arg name is already part of the C function pointer definition
            pass
        elif call and arg.type.proc_pointer:
            # Pass converted Fortran function pointer
            string = string + arg.name + ' ? &FORT_' + arg.name + ' : NULL'
        elif (bind or opts.no_vector) and not call and not arg.type.dt and arg.type.vec and not opts.array_as_ptr:
            string = string + arg.name + '[]'
        elif (not opts.no_vector) and call and not arg.type.dt and arg.type.vec:
            if arg.optional:
                string = string + arg.name + ' ? &(*' + arg.name + ')[0] : NULL'
            else:
                string = string + '&(*' + arg.name + ')[0]'
        else:
            string = string + arg.name
        # -------------------------------------------
        # Special string handling
        if call and arg.type.type=='CHARACTER' and not arg.intent=='inout':
            # For assumed-length intent(in) strings, no special
            # treatment needed here (we just pass along the character
            # array pointer)
            if not (arg.intent=='in' and arg.type.str_len.assumed):
                # Pass NULL if optional arg not present
                string = string + ' ? ' + arg.name + '_c__ : NULL'
        # Special handling for matrix arguments
        if call and arg.type.matrix and not opts.no_fmat:
            if arg.optional:
                string = string + ' ? ' + arg.name + '->data : NULL'
            else:
                string = string + '->data'
        # Special native handling of certain types:
        if call and arg.native:
            if arg.optional and arg.cpp_optional:
                string = string + ' ? ' + arg.name + '->{} : NULL'.format('data_ptr' if arg.type.dt == 'TYPE' else 'class_data_ptr')
            else:
                string = string + '->{}'.format('data_ptr' if arg.type.dt == 'TYPE' else 'class_data_ptr')
        elif not call and not bind and not definition and arg.cpp_optional:
            string = string + '=NULL'
        if proc.has_args_past_pos(ipos,bind):
            string = string + ', '
    return string
    
# TODO: clean up indent prefix: confusing for proc_pointer special treatment
def function_def_str(proc,bind=False,obj=None,call=False,dfrd_tbp=None,prefix='  '):
    """
    Return a string for a function declaration/definition/call.  There
    are four cases:
    -- C prototype declaration: bind
    -- Method declaration:      none
    -- Method definition:       obj
    -- C binding call:          bind, call

    dfrd_tbp points to corresponding deffered type bound procedure, if
    applicable.  This is needed because one abstract interface could
    be used for multiple TBP's, so can't identify the TBP just from
    the (abstract) procedure itself
    """
    s = ''
    # Add wrapper code for function pointers
    if call:
        declared_c_pointer = False
        for arg in proc.args.values():
            if arg.type.proc_pointer and not arg.fort_only():
                if not declared_c_pointer:
                    s = s + prefix + 'generic_fpointer c_pointer;\n'
                    declared_c_pointer = True
                s = s + prefix + 'c_pointer = (generic_fpointer) ' + arg.name + ';\n'
                s = s + prefix + 'long long FORT_' + arg.name + ';\n'
                s = s + prefix + 'if (' + arg.name + ') ' + mangle_name(fort_wrap_file,func_pointer_converter) + '(c_pointer' + ', &FORT_' + arg.name + ');\n'
    # Add wrapper code for strings
    if call:
        for arg in proc.args.values():
            if arg.type.type=='CHARACTER' and not arg.fort_only():
                str_len = arg.type.str_len.as_string()
                str_len_p1 = str_len + '+1'
                str_len_m1 = str_len + '-1'
            if arg.type.type=='CHARACTER' and not arg.fort_only() and arg.intent=='out':
                if arg.type.str_len.assumed:
                    # Make sure to initialize the str_length arg to 0,
                    # in case of a not-present optional, since a
                    # character array is statically declared with this
                    # length, even if the arg is not present (testing
                    # with gfortran indicates that in case of not
                    # present it passes 0 for the length)
                    s = s + prefix + 'fortran_charlen_t ' + arg.name + '_len__ = 0;\n'
                    s = s + prefix + 'if (' + arg.name + ') '+ arg.name + '_len__ = static_cast<fortran_charlen_t>('+ arg.name + '->length());\n'
                s = s + prefix + '// Declare memory to store output character data\n'
                s = s + prefix + 'char *' + arg.name + '_c__ = new char[' + str_len_p1 + '];\n'
                s = s + prefix + arg.name + '_c__[' + str_len + "] = '\\0';\n"
            elif arg.type.type=='CHARACTER' and not arg.fort_only() and arg.intent=='in':
                if arg.type.str_len.assumed:
                    s = s + prefix + 'int ' + arg.name + '_len__ = 0;\n'
                    s = s + prefix + 'if (' + arg.name + ') '+ arg.name+ '_len__ = static_cast<fortran_charlen_t>(strlen('+arg.name+')); // Protect Optional args\n'
                else:
                    s = s + prefix + '// Create C array for Fortran input string data\n'
                    s = s + prefix + 'char ' + arg.name + '_c__[' + str_len_p1 + '];\n'
                    s = s + prefix + 'if (' + arg.name + ') {\n'
                    s = s + prefix + '  strncpy(' + arg.name + '_c__, ' + arg.name + ', ' + str_len_p1 + '); ' +arg.name+'_c__['+str_len+'] = 0; // strncpy protects in case '+arg.name+' is too long\n'
                    s = s + prefix + '  for (size_t i=strlen('+arg.name+'_c__); i<'+str_len_p1+'; i++) '+arg.name+"_c__[i] = ' '; // Add whitespace for Fortran\n"
                    s = s + prefix + '}\n'
    # Add wrapper code for array size values
    if call:
        for arg in proc.args.values():
            if arg.type.is_array_size:
                s = s + prefix + 'int ' + arg.name + ' = static_cast<int>(' + proc.get_vec_size_parent(arg.name) + '->size());\n'
            elif arg.type.is_matrix_size:
                matrix_name, i = proc.get_matrix_size_parent(arg.name)
                size_method = ('num_rows()','num_cols()')[i]
                s = s + prefix + 'int ' + arg.name + ' = ' + matrix_name + '->' + size_method + ';\n'
    s = s + prefix
    # Make dummy class members static (so class doesn't need to be
    # instantiated).  Also make NOPASS methods static.
    if (proc.in_orphan_class or proc.nopass) and not bind and not call and not obj and not opts.global_orphans:
        s = s + 'static '        
    # Now write return type:
    if proc.retval and not (proc.retval.type.dt and bind and not proc.retval.pointer):
        if call:
            if not proc.has_post_call_wrapper_code():
                s = s + 'return '
            else:
                # Save return value and return after wrapper code below
                s = s + proc.retval.cpp_type(value=True,native=True) + ' __retval = '
            if proc.retval and proc.retval.pointer:
                s += 'new ' + proc.retval.type.type + '('
        else:
            if proc.retval.native and not bind:
                s += proc.retval.type.type + '* '
            else:
                s += proc.retval.cpp_type(value=True) + ' '
    elif not call:
        s = s + 'void '
    # Definition/declaration:
    if not bind:
        # Determine what the C++ method name will be
        if proc.tbp:
            method_name = proc.tbp.name
        elif dfrd_tbp:
            # proc is the abstract interface for a deferred tbp
            method_name = dfrd_tbp.name
        else:
            method_name= translate_name(proc.name)        
    if bind:
        s = s + mangle_name(proc.module,proc.name)
    elif obj and not opts.global_orphans:
        s = s + obj.cname + '::' + method_name
    else:
        s = s + method_name
    s = s + '(' + c_arg_list(proc,bind,call,obj!=None) + ')'
    if call and proc.retval and proc.retval.pointer:
            s += ')'
    if not obj:
        s = s + ';'
    if call:
        for arg in proc.args.values():
            # Special string handling for after the call
            if arg.type.type=='CHARACTER' and not arg.fort_only() and arg.intent=='out':
                str_len = arg.type.str_len.as_string()
                str_len_p1 = str_len + '+1'
                str_len_m1 = str_len + '-1'                
                s = s + '\n'
                s = s + prefix + 'if ('+arg.name+') {\n'
                s = s + prefix + '  // Trim trailing whitespace and assign character array to string:\n'
                s = s + prefix + '  for (int i=' + str_len_m1 + '; ' + arg.name + "_c__[i]==' '; i--) " + arg.name + "_c__[i] = '\\0';\n"
                s = s + prefix + '  ' + arg.name + '->assign(' + arg.name + '_c__);\n  }\n'
                s = s + prefix + 'delete[] ' + arg.name + '_c__;'
        if proc.retval and proc.has_post_call_wrapper_code():
            s = s + '\n' + prefix + 'return __retval;'
    return s

def write_constructor(file,object,fort_ctor=None):
    """Write the c++ code for the constructor, possibly including a
    call to a Fortran ctor"""
    # Not used for dummy class with orphan functions
    if object.name==orphan_classname or object.abstract:
        return
    file.write('// Constructor:\n')
    file.write(object.cname + '::' + object.cname)# + '() {\n')
    if fort_ctor:
        file.write('(' + c_arg_list(fort_ctor,definition=True) + ')' )
    else:
        file.write('()')
    file.write(' {\n')
    if object.is_class:
        # Use local variable and pass to _init
        pointer_name = '_data_ptr'
        file.write('  ADDRESS ' + pointer_name + ' = NULL;\n')
    else:
        # Use member variable
        pointer_name = 'data_ptr'
        file.write('  {} = NULL;\n'.format(pointer_name))
    # Allocate storage for Fortran derived type
    file.write('  ' + mangle_name(fort_wrap_file, 'allocate_' + object.name) + '(&{}); // Allocate Fortran derived type\n'.format(pointer_name))
    if object.is_class:
        file.write('  _init(_data_ptr, true);\n')
    else:
        file.write('  owns = true;\n')
    # If present, call Fortran ctor
    if fort_ctor:
        file.write(function_def_str(fort_ctor,bind=True,call=True) )
        file.write(' // Fortran Constructor\n')
        if init_func_def:
            file.write('  initialized = true;\n')
    else:
        if init_func_def:
            file.write('  initialized = false;\n')
    file.write('}\n\n')    

def write_destructor(file,object):
    """Write code for destructor"""
    if object.name==orphan_classname or object.abstract:
        return
    file.write('// Destructor:\n')
    file.write(object.cname + '::~' + object.cname + '() {\n')
    # Check for Fortran destructor
    for proc in object.procs:
        if proc.dtor:
            target = 'data_ptr' if proc.arglist[0].type.dt=='TYPE' else 'class_data_ptr'
            prefix = 'if (owns && initialized) ' if init_func_def else 'if (owns) '
            file.write('  ' + prefix + mangle_name(proc.module,proc.name) + '(' + target)
            # Add NULL for any optional arguments (only optional
            # arguments are allowed in the destructor call)
            for i in range(proc.nargs-1):
                file.write(', NULL')
            file.write('); // Fortran Destructor\n')
    # Deallocate Fortran derived type
    file.write('  if (owns) ' + mangle_name(fort_wrap_file,'deallocate_'+object.name) + '(data_ptr); // Deallocate Fortran derived type\n')
    file.write('}\n\n')    

def write_class(object):

    # Skip over objects in the exclusion list:
    if object.name.lower() in name_exclusions:
        return

    # First write header file:
    file = open( include_output_dir+'/' + object.cname + '.h', 'w')
    file.write(HEADER_STRING + '\n')
    file.write('#ifndef ' + object.cname.upper() + '_H_\n')
    file.write('#define ' + object.cname.upper() + '_H_\n\n')

    # Write the DLLEXPORT macro into each individual header instead of putting it in a top-level header, since this makes it easier to process with Swig
    file.write(DLLEXPORT_MACRO + '\n\n')
    
    if SWIG:
        # Needs to be before the include's in the case of swig -includeall
        file.write('\n#ifndef SWIG // Protect declarations from SWIG\n')
    file.write('#include <cstdlib>\n') # Needed for NULL
    if not opts.no_vector:
        file.write('#include <vector>\n')
    if complex_used:
        file.write('#include <complex>\n')
    file.write('#include "' + misc_defs_filename + '"\n')
    includes = get_native_includes(object)
    if object.name in includes:
        includes.remove(object.name) # Remove self
    for include in includes:
        if include.startswith('<'):
            file.write('#include ' + include + '\n')
        else:
            file.write('#include "' + translate_name(include) + '.h"\n')
    # Declare external vtab data
    if object.is_class:
        file.write('\n// Declare external vtab data:\n')
        file.write('extern int {0}; // int is dummy data type\n'.format(vtab_symbol(object.module, object.name)))
    # C Bindings
    file.write('\nextern "C" {\n')
    # Write bindings for allocate/deallocate funcs
    if object.name!=orphan_classname and not object.abstract:
        file.write('  void ' + mangle_name(fort_wrap_file, 'allocate_'+object.name) + '(ADDRESS *caddr);\n')
        file.write('  void ' + mangle_name(fort_wrap_file, 'deallocate_'+object.name) + '(ADDRESS caddr);\n')
    for proc in object.procs:
        file.write(function_def_str(proc,bind=True) + '\n')
    file.write('}\n')

    if SWIG:
        file.write('#endif // SWIG\n')
    file.write('\n')
    
    if not opts.global_orphans:
        write_cpp_dox_comments(file,object.comment)
        file.write('class DLLEXPORT ' + object.cname + ' ')
        if object.extends:
            file.write(': public ' + object.extends + ' ')
        file.write('{\n\n')
        if object.abstract:
            file.write('protected:\n  // {0} can not be instantiated\n  {0}() {{}}\n\n'.format(object.cname))
        if object.is_class and not object.abstract:
            file.write('private:\n')
            file.write('  void _init(ADDRESS p, bool memOwn);\n\n')
        # Special pointer constructor:
        if object.has_pointer_ctor and not object.abstract:
            file.write('private:\n')
            file.write('  ' + object.cname + '(ADDRESS p);\n')
            for other in objects.values():
                if object.name in other.pointer_return_types:
                    file.write('  friend class ' + other.name+ '; // For accessing pointer constructor\n')
            file.write('\n')
        file.write('public:\n')
    # Constructor:
    fort_ctors = object.ctor_list()
    if fort_ctors:
        for fort_ctor in fort_ctors:
            write_cpp_dox_comments(file, fort_ctor.comment, fort_ctor.arglist, prefix=2)
            file.write('  ' + object.cname + '(' + c_arg_list(fort_ctor,bind=False,call=False,definition=False) + ');\n')
    elif not object.name==orphan_classname and not object.abstract:
        # Don't declare default constructor (or destructor, below) for
        # the dummy class
        file.write('  ' + object.cname + '();\n')
    # Desctructor:
    if not object.name==orphan_classname:
        if object.abstract:
            file.write('  virtual ~' + object.cname + '() {}\n\n')
        else:
            file.write('  ~' + object.cname + '();\n\n')
    # Method declarations
    for proc in object.procs:
        # dtors are automatically called by C++ destructor.  A
        # separate function is not created by default, but can be
        # enabled by adding an %include for the dtor
        if proc.ctor or (proc.dtor and not proc.name.lower() in name_inclusions):
            continue
        write_cpp_dox_comments(file, proc.comment, proc.arglist, proc.retval, prefix=2)
        file.write(function_def_str(proc) + '\n\n')
    # Check for pure virtual methods (which have no directly
    # associated procedure)
    for tbp in object.tbps.values():
        # C++ doesn't support static virtual methods, so don't write DEFERRED, NOPASS methods into the C++ wrapper
        if tbp.deferred and not tbp.nopass:
            # Note: this lookup doesn't respect the module USE
            # hierarchy and could potentially point to wrong
            # abstract_interfaces if the project contains multiple
            # abstract_interfaces with different names, in different
            # modules
            if tbp.interface not in abstract_interfaces:
                error('abstract interface {0} for type bound procedure {1} not found (may not be interoperable)'.format(tbp.interface, tbp.name))
            else:
                file.write('  virtual ' + function_def_str(abstract_interfaces[tbp.interface], dfrd_tbp=tbp, prefix='')[:-1] + ' = 0;\n\n')
    #file.write('\nprivate:\n')
    if object.has_pointer_ctor:
        file.write('  void _disown();\n')
        file.write('  void _acquire();\n\n')
    if object.name!=orphan_classname and not object.extends:
        file.write('  ADDRESS data_ptr;\n')
        if object.is_class:
            file.write('  FClassContainer class_data;\n')
            file.write('  FClassContainer* class_data_ptr;\n')
        file.write('\nprotected:\n')
        file.write('  bool owns;\n')
        if init_func_def:
            file.write('  bool initialized;\n')
    if not opts.global_orphans:
        file.write('};\n\n')
    if object.name == orphan_classname:
        # Write out constant/enum defs inside a class def.  For now,
        # this small class def piggybacks off the orphan class header
        # file.  When wrapping in Swig for Python, it makes no
        # difference, since the __init__ file brings in everything in
        # this module
        if len(enumerations) > 0:
            file.write('class ' + constants_classname + ' {\n')
            file.write('public:\n')
            for enum_set in enumerations:
                file.write('  enum { ')
                for i,enum in enumerate(enum_set):
                    file.write('{0}{1}'.format(enum,', ' if i+1<len(enum_set) else ''))
                file.write(' };\n')
            file.write('};\n\n')
    file.write('#endif /* ' + object.cname.upper() + '_H_ */\n')
    file.close()


    # Write method code to cpp file
    file = open( code_output_dir+'/' + object.cname + '.cpp', 'w')
    file.write(HEADER_STRING + '\n')
    if stringh_used:
        file.write('#include <cstring> // For strcpy\n')
    file.write('#include "' + object.cname + '.h"\n\n')
    if object.is_class and not object.abstract:
        file.write('// Initialization code to set up class pointers\n')
        file.write('void ' + object.cname + '::' + '_init(ADDRESS p, bool memOwn) {\n')
        file.write('  data_ptr = p;\n')
        file.write('  owns = memOwn;\n')
        # Class data must be set up before calling constructor, in
        # case constructor uses CLASS argument
        file.write('  class_data.vptr = &{0}; // Get pointer to vtab\n'.format(vtab_symbol(object.module, object.name)))
        file.write('  class_data.data = data_ptr;\n')
        file.write('  class_data_ptr = &class_data;\n')
        file.write('}\n\n')
    # Write special pointer constructor:
    if object.has_pointer_ctor and not object.abstract:
        file.write('// Pointer constructor:\n')
        file.write(object.cname + '::' + object.cname + '(ADDRESS p) {\n')
        if object.is_class:
            file.write('  _init(p, false);\n')
        else:
            file.write('  data_ptr = p;\n')
            file.write('  owns = false;\n')
        if init_func_def:
            file.write('  initialized = false;\n')
        file.write('}\n\n')
    # Constructor(s):
    if fort_ctors:
        for fort_ctor in fort_ctors:
            write_constructor(file,object,fort_ctor)
    else:
        write_constructor(file,object)
    # Destructor
    write_destructor(file,object)
    
    # Other methods:
    for proc in object.procs:
        if proc.ctor or (proc.dtor and not proc.name.lower() in name_inclusions):
            continue
        file.write(function_def_str(proc,obj=object,prefix='') + ' {\n')
        if proc.dtor and init_func_def:
            file.write('  if (initialized) ')
        file.write(function_def_str(proc,bind=True,call=True,prefix='  ') + '\n')
        if proc.dtor and init_func_def:
            file.write('  initialized = false;\n')
        elif init_func_def and init_func_def.match(proc.name):
            file.write('  initialized = true;\n')
        file.write('}\n\n')

    if object.has_pointer_ctor:
        file.write('void ' + object.cname + '::_disown() { owns = false; }\n')
        init = 'initialized = true; ' if init_func_def else ''
        file.write('void ' + object.cname + '::_acquire() { owns = true; ' + init + '}\n')

    file.close()

def get_native_includes(object):
    """
    After method association, check which native types an object uses
    and return a corresponding string list of include file

    This will also add the include needed for inheritance
    """
    includes = set()
    for proc in object.procs:
        for argname,arg in proc.args.items():
            if arg.native:
                includes.add(arg.type.type)
            if arg.type.matrix and not opts.no_fmat:
                includes.add(matrix_classname)
            if arg.type.type=='CHARACTER' and arg.intent!='in':
                if opts.std_string:
                    # The use of angle brackets is handled specially
                    # in the output code
                    includes.add('<string>')
                else:
                    includes.add(string_classname)
        if proc.retval and proc.retval.type.dt and proc.retval.pointer:
            includes.add(proc.retval.type.type)
    # For inheritance:
    if object.extends:
        includes.add(object.extends)
    return includes

def write_global_header_file():
    f = open(include_output_dir+'/' + opts.main_header + '.h','w')
    f.write(HEADER_STRING + '\n')
    for obj in objects.values():
        f.write('#include "' + translate_name(obj.name) + '.h"\n')
    if matrix_used:
        f.write('#include "' + matrix_classname + '.h"\n')
    f.write('\n')
    f.close()


def write_misc_defs():
    f = open(include_output_dir+'/' + misc_defs_filename, 'w')
    f.write(HEADER_STRING + '\n')
    f.write('#ifndef ' + misc_defs_filename.upper()[:-2] + '_H_\n')
    f.write('#define ' + misc_defs_filename.upper()[:-2] + '_H_\n\n')

    f.write("""#if __GNUC__ > 7
#include <cstdlib>
typedef std::size_t fortran_charlen_t;
#else
typedef int fortran_charlen_t;
#endif\n\n""")

    f.write('typedef void(*generic_fpointer)(void);\n')
    f.write('typedef void* ADDRESS;\n\n')
    if fort_class_used:
        f.write('struct FClassContainer {\n  ADDRESS data;\n  ADDRESS vptr;\n};\n\n')
    f.write('extern "C" {\n')
    if compiler == 'g95':
        f.write('  /* g95_runtime_start and stop are supposed to be called\n')
        f.write('     to start and stop the g95 runtime engine, but I have not\n')
        f.write('     had any problems when leaving them out.  The only thing\n')
        f.write('     I have noticed is that a bunch of "still reachable" memory\n')
        f.write('     reported by valgrind goes away when a g95_runtime_stop call\n')
        f.write('     is used */\n')
        f.write('  void g95_runtime_start(int narg, char* args[]);\n')
        f.write('  void g95_runtime_stop(void);\n\n')
    if proc_pointer_used:
        f.write('  void ' + mangle_name(fort_wrap_file,func_pointer_converter) + '(generic_fpointer c_pointer, void* fortran_pointer);\n')
    f.write('}\n')
    f.write('\n#endif /* ' + misc_defs_filename.upper()[:-2] + '_H_ */\n')
    f.close()

def write_matrix_class():
    if not matrix_used:
        return
    comments = ['A templated class for working with Matrices that store data', 'internally in Fortran order.', '', 'From C++, the data are accessed in the natural order, using', '<tt>x(row,column)</tt> notation, starting with base index 0']
    
    f = open(include_output_dir+'/' + matrix_classname + '.h', 'w')
    f.write(HEADER_STRING + '\n')
    f.write('#ifndef ' + matrix_classname.upper() + '_H_\n')
    f.write('#define ' + matrix_classname.upper() + '_H_\n\n')
    f.write('#include <cstdlib>\n#include <cassert>\n\n')
    write_cpp_dox_comments(f, comments)
    f.write('template <class T>\nclass ' + matrix_classname + '{\n\n')
    f.write('  int nrows, ncols;\n')
    f.write('  bool owns;\n')
    f.write('\npublic:\n\n  ')    
    f.write('  T *data;\n\n')
    
    write_cpp_dox_comments(f, ['Create a matrix with m rows and n columns','','Allocates new memory'], prefix=2)
    f.write('  ' + matrix_classname + '(int m, int n) {\n')
    f.write('    data = NULL;\n    assert(m>0 && n>0);\n    nrows=m; ncols=n;\n')
    f.write('    data = (T*) calloc( m*n, sizeof(T) );\n    owns = true;\n  }\n\n')

    write_cpp_dox_comments(f, ['Set up a pointer to existing contiguous array data (in Fortran order)'], prefix=2)
    f.write('  ' + matrix_classname + '(int m, int n, T *data_ptr) {\n')
    f.write('    data = data_ptr;\n    assert(m>0 && n>0);\n    nrows=m; ncols=n;\n')
    f.write('    owns = false;\n  }\n\n')
    
    f.write('  ~' + matrix_classname + '() { if(data && owns) free(data); }\n\n')    
    write_cpp_dox_comments(f, ['Provides element access via the parentheses operator.','','The base index is 0'], prefix=2)
    f.write('  T& operator()(int i, int j) {\n')
    f.write('    assert( i>=0 && i<nrows && j>=0 && j<ncols );\n')
    f.write('    // i--; j--; // Adjust base\n')
    f.write('    return data[j*nrows+i];\n  }\n\n')
    write_cpp_dox_comments(f, ['Get number of rows'], prefix=2)
    f.write('  inline int num_rows(void) const { return nrows; }\n\n')
    write_cpp_dox_comments(f, ['Get number of columns'], prefix=2)
    f.write('  inline int num_cols(void) const { return ncols; }\n\n')
    f.write('};\n\n')
    f.write('#endif /* ' + matrix_classname.upper() + '_H_ */\n')
    f.write('\n\n// Local Variables:\n// mode: c++\n// End:\n')
    f.close()

def write_string_class():
    """When the option --no-std-string is used, create a wrapper class with similar functionality.
    
    The reason for doing this is it can work around some C++ library
    conflicts on Windows when linking programs against e.g. Java or Qt
    libraries.

    Separate code and header files are used for this class, since it
    is not a template class.
    """
    if not string_class_used:
        return

    # Header files:
    comments = ['Simple wrapper class for handling dynamic allocation of string data','','Used by FortWrap to handle wrapping of character output arguments.  Emulates some of the basic functionality of std::string, but can be used as a way to remove dependency on std::string and avoid C++ library conflicts in some cases']

    f = open(include_output_dir+'/' + string_classname + '.h', 'w')
    f.write(HEADER_STRING + '\n')
    f.write('#ifndef ' + string_classname.upper() + '_H_\n')
    f.write('#define ' + string_classname.upper() + '_H_\n\n')

    f.write(DLLEXPORT_MACRO + '\n\n')
    
    f.write('#include <cstdlib>\n#include <cstring>\n\n')
    write_cpp_dox_comments(f, comments)
    body = """\
class DLLEXPORT $CLASSNAME{
  size_t length_;
  char* data_;
  
 public:

  $CLASSNAME();

  $CLASSNAME(size_t length);

  ~$CLASSNAME();

  size_t length(void);

  void resize(size_t length);

  void assign(const char* s);

  int compare(const char* s) const;

  char* data(void);

  char* c_str(void);
};
""".replace('$CLASSNAME', string_classname)
    f.write(body)
    f.write('\n\n')
    f.write('#endif /* ' + string_classname.upper() + '_H_ */\n')
    f.write('\n\n// Local Variables:\n// mode: c++\n// End:\n')
    f.close()
    
    # Code file:
    f = open(include_output_dir+'/' + string_classname + '.cpp', 'w')
    f.write(HEADER_STRING + '\n')
    
    f.write('#include "' + string_classname + '.h"\n\n')
    body = """\
$CLASSNAME::$CLASSNAME() : length_(0), data_(NULL) {}

$CLASSNAME::$CLASSNAME(size_t length) : length_(length), data_(NULL) {
  if (length>0) data_ = (char*) calloc(length+1, sizeof(char));
}

$CLASSNAME::~$CLASSNAME() { if(data_) free(data_); }

size_t $CLASSNAME::length(void) { return length_; }

void $CLASSNAME::resize(size_t length) {
  if (data_) free(data_);
  data_ = (char*) calloc(length+1, sizeof(char));
  length_ = length;
}

void $CLASSNAME::assign(const char* s) {
  length_ = strlen(s);
  resize(length_);
  strncpy(data_, s, length_);
}

int $CLASSNAME::compare(const char* s) const {
  return strncmp(data_, s, length_);
}

char* $CLASSNAME::data(void) { return data_; }

char* $CLASSNAME::c_str(void) { return data_; }
""".replace('$CLASSNAME', string_classname)
    f.write(body)
    f.close()
    
def write_fortran_wrapper():
    """
    Write the Fortran code for allocating/deallocating Fortran derived
    types from C pointers
    """
    # TODO: fix hack for testing whether we need to write this file
    # Hack: check for real (non-orphan) methods
    count = 0
    for obj in objects.values():
        if obj.name != orphan_classname:
            count += 1
    if count == 0 and not proc_pointer_used:
        return
    # Build list of modules we need to USE
    use_mods = set()
    for obj in objects.values():
        if obj.name != orphan_classname:
            use_mods.add(obj.module)
    f = open(fort_output_dir+'/' + fort_wrap_file + '.f90', "w")
    #f.write(HEADER_STRING + '\n') # Wrong comment style
    f.write('MODULE ' + fort_wrap_file + '\n\n')
    # USE the necessary modules:
    for mod in use_mods:
        f.write('USE ' + mod + '\n')
    f.write('USE ISO_C_BINDING\n\nCONTAINS\n\n')
    if proc_pointer_used:
        f.write('  SUBROUTINE '+func_pointer_converter+'(cpointer,fpointer)\n')
        f.write('    USE ISO_C_BINDING\n')
        f.write('    TYPE(C_FUNPTR), VALUE :: cpointer\n')
        f.write('    PROCEDURE(), POINTER :: fpointer\n')
        f.write('    CALL C_F_PROCPOINTER(cpointer,fpointer)\n')
        f.write('  END SUBROUTINE '+func_pointer_converter+'\n\n')
    for obj in objects.values():
        if obj.name == orphan_classname or obj.abstract:
            continue
        cptr = obj.name + '_cptr'
        fptr = obj.name + '_fptr'
        # Write allocate function
        f.write(' SUBROUTINE allocate_' + obj.name + '(' + cptr + ')\n')
        f.write('    TYPE (C_PTR) :: ' + cptr + '\n\n')
        f.write('    TYPE (' + obj.name + '), POINTER :: ' + fptr + '\n\n')
        f.write('    ALLOCATE( ' + fptr + ' )\n')
        f.write('    ' + cptr + ' = C_LOC(' + fptr + ')\n')
        f.write('  END SUBROUTINE allocate_' + obj.name + '\n\n')
        # Write deallocate function
        f.write(' SUBROUTINE deallocate_' + obj.name + '(' + cptr + ')\n')
        f.write('    TYPE (C_PTR), VALUE :: ' + cptr + '\n\n')
        f.write('    TYPE (' + obj.name + '), POINTER :: ' + fptr + '\n\n')
        f.write('    CALL C_F_POINTER(' + cptr + ', ' + fptr + ')\n')
        f.write('    DEALLOCATE( ' + fptr + ' )\n')
        f.write('  END SUBROUTINE deallocate_' + obj.name + '\n\n')        
    f.write('END MODULE ' + fort_wrap_file + '\n')
                

def clean_directories():
    """
    Remove old files from output directories before writing new ones
    """
    files = set()
    files = files.union( set( glob.glob(code_output_dir+'/*.cpp') ) )
    files = files.union( set( glob.glob(code_output_dir+'/*.o') ) )
    files = files.union( set( glob.glob(include_output_dir+'/*.h') ) )
    files = files.union( set( glob.glob(fort_output_dir+'/*.f90') ) )
    files = files.union( set( glob.glob(fort_output_dir+'/*.o') ) )
    for f in files:
        os.remove(f)
        

def internal_error():
    sys.stderr.write('FortWrap internal error encountered\n\n')
    sys.stderr.write('Please submit a bug report to mcfarljm@gmail.com which includes the error log\n'+ERROR_FILE_NAME+' and the Fortran source code being wrapped\n')
    f = open(ERROR_FILE_NAME,'w')
    f.write('FortWrap version ' + VERSION + '\n')
    f.write('Platform: ' + sys.platform + '\n\n')
    traceback.print_exc(file=f)
    f.close()
    sys.exit(1)


# Class for parsing the configuration file
class ConfigurationFile(object):
    def __init__(self,fname):
        self.fname = fname
        if fname:
            try:
                self.f = open(fname)
            except:
                error("Error opening interface file: " + fname)
                return
            self.process()

    def process(self):
        global name_exclusions, name_inclusions, name_substitutions, ctor_def, dtor_def, init_func_def
        for line_num,line in enumerate(self.f):
            if not line.startswith('%'):
                continue
            words = [w.lower() for w in line.split()]
            if words[0] == '%ignore':
                if len(words) == 2:
                    name_exclusions.add( words[1] )
                else:
                    self.bad_decl(line_num+1)
                    continue
            elif words[0] == '%hide':
                if len(words) == 3:
                    proc_arg_exclusions.add( tuple(words[1:]) )
                else:
                    self.bad_decl(line_num+1)
                    continue
            elif words[0] == '%include':
                if len(words) == 2:
                    name_inclusions.add( words[1] )
                else:
                    self.bad_decl(line_num+1)
                    continue
            elif words[0] == '%rename':
                if len(words) == 3:
                    # Note: want new C++ name to preserve case
                    new_name = line.split()[2]
                    name_substitutions[words[1]] = new_name
                else:
                    self.bad_decl(line_num+1)
                    continue
            elif words[0] == '%pattern':
                if len(words) == 2 or len(words) == 3:
                    if len(words) == 2:
                        replace_pattern = ''
                    else:
                        # Note: want new C++ name to preserve case
                        replace_pattern = line.split()[2]
                    match_pattern = words[1]
                    pattern_substitutions.append((re.compile(match_pattern, re.IGNORECASE), replace_pattern))
                else:
                    self.bad_decl(line_num+1)
                    continue
            elif words[0] == '%ctor':
                ctor_def = re.compile(line.strip().split('%ctor ')[1], re.IGNORECASE)
            elif words[0] == '%dtor':
                dtor_def = re.compile(line.strip().split('%dtor ')[1], re.IGNORECASE)
            elif words[0] == '%init':
                init_func_def = re.compile(line.strip().split('%init ')[1], re.IGNORECASE)
            else:
                error("Unrecognized declaration in interface file: {}".format(words[0]))
                
    def bad_decl(self,line_num):
        error("{}, line {} <-- bad declaration".format(self.fname, line_num))


# Class for parsing command line options
class Options(object):
    def __init__(self):
        self.parse_args()
        self.check_args()
        self.assign_globals()
    
    def parse_args(self):
        """Use argparse to parse command arguments"""

        parser = argparse.ArgumentParser(prog='fortwrap')
        parser.add_argument('-v','--version', action='version', version='%(prog)s '+VERSION)
        parser.add_argument('files', nargs='*', help='files to process')
        parser.add_argument('-n', '--dry-run', action='store_true', help='run parser but do not generate any wrapper code (dry run)')
        parser.add_argument('-c', '--compiler', default=compiler, choices=['gfortran','g95'], help='use name mangling for Fortran compiler COMPILER.  Only supports g95 and gfortran (Default=%(default)s)')
        parser.add_argument('-g','--glob', action='store_true', help='wrap source files found in current directory')
        parser.add_argument('-d','--directory', default='.', help='output generated wrapper code to DIRECTORY')
        parser.add_argument('--file-list', help='Read list of Fortran source files to parser from file FILE_LIST.  The format is a newline-separated list of filenames with full or relative paths.')
        parser.add_argument('-i', '--config-file', help='read interface configuration file CONFIG_FILE')
        parser.add_argument('--no-vector', action='store_true', help='wrap 1-D array arguments as C-style arrays instead of C++ std::vector containers')
        parser.add_argument('--no-fmat', action='store_true', help='do not wrap 2-D array arguments with the FortranMatrix type')
        parser.add_argument('--array-as-ptr', action='store_true', help="wrap 1-D arrays with '*' instead of '[]'.  Implies --no-vector")
        parser.add_argument('--no-std-string', action='store_true', help='wrap character outputs using a wrapper class instead of std::string')
        parser.add_argument('--dummy-class', default='FortFuncs', help='use DUMMY_CLASS as the name of the dummy class used to wrap non-method procedures')
        parser.add_argument('--global-funcs', action='store_true', help='wrap non-method procedures as global functions instead of static methods of a dummy class')
        parser.add_argument('--no-orphans', action='store_true', help='do not by default wrap non-method procedures.  They can still be wrapped by using %%include directives')
        parser.add_argument('--no-W-not-wrapped', action='store_true', help='do not warn about procedures that were not wrapped')
        parser.add_argument('--main-header', default='FortWrap', help='Use MAIN_HEADER as name of the main header file (default=%(default)s)')
        parser.add_argument('--constants-class', default='FortConstants', help='use CONSTANTS_CLASS as name of the class for wrapping enumerations (default=%(default)s)')
        parser.add_argument('--document-all-params', action='store_true', help='write doxygen \\param comment for all arguments')
        # Not documenting, as this option could be dangerous, although it is
        # protected from -d.  help='remove all wrapper-related files from
        # wrapper code directory before generating new code.  Requires -d.
        # Warning: this deletes files.  Use with caution and assume it will
        # delete everything in the wrapper directory'
        parser.add_argument('--clean', action='store_true', help=argparse.SUPPRESS)

        parser.parse_args(namespace=self)

    def check_args(self):
        """Additional validity checking an value setting not done automatically by argparse"""
        if self.directory != '.':
            if not os.path.isdir(self.directory):
                error('Directory does not exist: ' + self.directory)
                sys.exit(2)
        if self.clean and self.directory=='.':
            error('Cleaning wrapper code output dire requires -d')
            sys.exit(2)

        if self.array_as_ptr:
            self.no_vector = True

        self.global_orphans = self.global_funcs
        self.std_string = not self.no_std_string
        self.warn_not_wrapped = not self.no_W_not_wrapped
        if self.main_header != 'FortWrap':
            self.main_header = self.main_header.split('.h')[0]

    def assign_globals(self):
        """Assign certain options to global variables"""
        global code_output_dir, include_output_dir, fort_output_dir, string_object_type, constants_classname, compiler, orphan_classname, file_list

        file_list = self.files
        compiler = self.compiler
        orphan_classname = self.dummy_class

        if self.directory != '.':
            code_output_dir = self.directory
            include_output_dir = self.directory
            fort_output_dir = self.directory

        if self.no_std_string:
            string_object_type = string_classname

        constants_classname = self.constants_class



# COMMANDS ==========================================

if __name__ == "__main__":

    try:

        file_list = []

        opts = Options()

        configs = ConfigurationFile(opts.config_file)

        if opts.clean:
            clean_directories()

        if opts.file_list:
            try:
                f = open(opts.file_list)
            except IOError:
                error('Unable to open file list: ' + opts.file_list)
                sys.exit(3)
            for line in f:
                if not line.strip().startswith('#') and re.search('\w', line):
                    file_list.append( line.strip() )
            f.close()
            print("LOADED", len(file_list), 'FILES FROM LIST')

        if opts.glob:
            file_list += glob.glob('*.[fF]90')

        if not file_list:
            error("No source files")
            sys.exit(3)

        fcount = 0  # Counts valid files
        for f in file_list:
            fcount += parse_file(f)
        if fcount==0:
            error("No source files")
            sys.exit(3)

        if opts.warn_not_wrapped and len(not_wrapped) > 0:
            warning("Some procedures not wrapped:\n " + '\n '.join(not_wrapped))

        # Prevent writing any files if there is nothing to wrap
        if len(procedures)==0:
            error("No procedures to wrap")
            sys.exit(4)

        associate_procedures()

        if opts.dry_run:
            sys.exit(0)

        for obj in objects.values():
            write_class(obj)

        write_global_header_file()
        write_misc_defs()
        write_matrix_class()
        if not opts.std_string:
            write_string_class()
        write_fortran_wrapper()

        if fort_class_used:
            warning("support for wrapping abstract types and type extension is experimental")

        sys.exit(0)

    except SystemExit:
        # Raised by sys.exit
        raise

    except:
        internal_error()
