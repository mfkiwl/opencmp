########################################################################################################################
# Taken from the pyparsing examples (with modifications).                                                              #
#                                                                                                                      #
# Copyright 2003-2019 Paul McGuire                                                                                     #
#                                                                                                                      #
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE #
# WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS   #
# OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR  #
# OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.     #
########################################################################################################################
# Copyright 2021 the authors (see AUTHORS file for full list).                                                         #
#                                                                                                                      #
# This file is part of OpenCMP.                                                                                        #
#                                                                                                                      #
# OpenCMP is free software: you can redistribute it and/or modify it under the terms of the GNU Lesser General Public  #
# License as published by the Free Software Foundation, either version 2.1 of the License, or (at your option) any     #
# later version.                                                                                                       #
#                                                                                                                      #
# OpenCMP is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied        #
# warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public License for more  #
# details.                                                                                                             #
#                                                                                                                      #
# You should have received a copy of the GNU Lesser General Public License along with OpenCMP. If not, see             #
# <https://www.gnu.org/licenses/>.                                                                                     #
########################################################################################################################

import pyparsing as pp
import math
import ngsolve as ngs
from ngsolve import Mesh, Parameter, CoefficientFunction
import operator
from typing import List, Tuple, Union, Dict, Any, Optional
from helpers.math import tanh, sig, H_s, ramp_cos
import sys


def parse_to_arith(expr_stack: List[Union[str, Tuple[str, int]]]):
    """
    Creates a parser to turn a string into a series of arithmetic operations.

    Args:
        expr_stack: Empty list to parse the string into.

    Returns:
        The parser.
    """

    def push_first(token):
        """ Pushes the token to the top of the stack. Used to enforce correct order of operations. """
        expr_stack.append(token[0])

        return

    def push_unary_minus(token):
        """ Replaces '-' with unary negation where appropriate. """
        for item in token:
            if item == '-':
                expr_stack.append('unary -')
            else:
                break

        return

    def insert_func_tuple(token):
        """ Turns a list into a tuple corresponding to a function operator and its arguments. """
        func = token.pop(0)
        num_args = len(token[0])
        token.insert(0, (func, num_args))

        return token

    def insert_vec_tuple(token):
        """ Turns a list into a tuple corresponding to the 'vec' operator and the components of the vector. """
        num_args = len(token[0])
        token.insert(0, ('vec', num_args))

        return token

    e = pp.CaselessKeyword('e')
    pi = pp.CaselessKeyword('pi')

    x = pp.Keyword('x')
    y = pp.Keyword('y')
    z = pp.Keyword('z')
    t = pp.Keyword('t')

    sci_notation = pp.Regex(r'[+-]?\d+(?:\.\d*)?(?:[eE][+-]?\d+)?')  # Regex is the preferred way to do this.

    func = pp.Word(pp.alphas, pp.alphas + '_')

    plus, minus, mult, div = map(pp.Literal, '+-*/')
    l_par, r_par = map(pp.Suppress, '()')
    l_brac, r_brac = map(pp.Suppress, '[]')

    add_op = plus | minus
    mult_op = mult | div
    exp_op = pp.Literal('^')

    expr = pp.Forward()
    expr_list = pp.delimitedList(pp.Group(expr))  # Group keeps the list hierarchy denoted by any parentheses.

    # Identifies function calls and replaces then with the appropriate tuple.
    func_call = (func + l_par - pp.Group(expr_list) + r_par).setParseAction(insert_func_tuple)
    vec_call = (l_brac - pp.Group(expr_list) + r_brac).setParseAction(insert_vec_tuple)
    varying_call = (pp.Keyword('IMPORT') + l_par - pp.Group(expr_list) + r_par).setParseAction(insert_func_tuple)

    # Identifies atomic expressions.
    atom = (add_op[...] + (
            (varying_call | func_call | pi | e | x | y | z | t | sci_notation | func | vec_call).setParseAction(push_first) |
            pp.Group(l_par + expr + r_par))).setParseAction(push_unary_minus)

    # Required to correctly compute exponents.
    factor = pp.Forward()
    factor <<= atom + (exp_op + factor).setParseAction(push_first)[...]
    term = factor + (mult_op + factor).setParseAction(push_first)[...]
    expr <<= term + (add_op + term).setParseAction(push_first)[...]
    arith_expr = expr

    return arith_expr


def evaluate_arith_stack(stack: List[Union[str, Tuple[str, int]]], import_dir: str, t_param: Optional[Parameter],
                         new_variables: Dict[str, Any], mesh: Optional[Mesh] = None)\
        -> Tuple[Union[str, float, CoefficientFunction], Union[bool, callable]]:
    """
    Function to turn a list of strings corresponding to arithmetic operations into those operations as Python code.

    Args:
        stack: The list of strings.
        import_dir: The path to the main run directory containing the file from which to import any Python functions.
        t_param: Parameter representing the current time.
        new_variables: A dictionary of any new model variables and their values.
        mesh: The mesh used by the model

    Returns:
        Tuple[Union[str, float, CoefficientFunction], Union[bool, callable]]:
            - val: The Python code.
            - variable_eval: Whether or not the expression contains any of the new model variables (would need to be
                re-parsed if their values change). If the expression involves importing a Python function return that 
                Python function instead.
    """

    # Map operator symbols to corresponding arithmetic operations.
    epsilon = 1e-12
    operations = {'+': operator.add,
                  '-': operator.sub,
                  '*': operator.mul,
                  '/': operator.truediv,
                  '^': operator.pow}

    funcs = {'sin': ngs.sin,
             'cos': ngs.cos,
             'tan': ngs.tan,
             'exp': ngs.exp,
             'tanh': tanh,
             'sig': sig,
             'H': H_s,
             'abs': abs,
             'trunc': int,
             'round': round,
             'sqrt': ngs.sqrt,
             'sgn': lambda a: -1 if a < -epsilon else 1 if a > epsilon else 0,
             'vec': lambda *a: ngs.CoefficientFunction(a),
             'ramp': ramp_cos}

    constants = {'pi': math.pi,
                 'e': math.e,
                 'None': None,
                 'True': True,
                 'False': False}

    variables = {'x': ngs.x,
                 'y': ngs.y,
                 'z': ngs.z,
                 't': t_param}

    # Set a flag to denote that the expression is a function of one or more of the model variables and would need to be
    # re-parsed when that variable's value changes.
    variable_eval = False

    op, num_args = stack.pop(), 0

    if isinstance(op, tuple):
        op, num_args = op

    if op == 'unary -':
        res, variable_eval = evaluate_arith_stack(stack, import_dir, t_param, new_variables, mesh)
        return -res, variable_eval

    elif op in '+-*/^':
        # Note: operands are pushed onto the stack in reverse order.
        op2, variable_eval2 = evaluate_arith_stack(stack, import_dir, t_param, new_variables, mesh)
        op1, variable_eval1 = evaluate_arith_stack(stack, import_dir, t_param, new_variables, mesh)
        return operations[op](op1, op2), variable_eval2 or variable_eval1

    elif op in constants:
        return constants[op], variable_eval

    elif op in variables:
        return variables[op], variable_eval

    elif op in new_variables.keys():
        variable_eval = True
        if isinstance(new_variables[op], list):
            return new_variables[op][0], variable_eval
        else:
            return new_variables[op], variable_eval

    elif op in funcs:
        # Note: args are pushed onto the stack in reverse order.
        args = []
        for i in range(num_args):
            arg, variable_eval_tmp = evaluate_arith_stack(stack, import_dir, t_param, new_variables, mesh)
            variable_eval = variable_eval or variable_eval_tmp
            args.append(arg)
        args = reversed(args)
        return funcs[op](*args), variable_eval

    elif op == 'IMPORT':
        # Treat the same as a function evaluation but now import the function to apply from a Python file.
        #
        # Must take one single argument, which is the name of the function to import.
        assert num_args == 1
        import_name = stack.pop()

        if mesh is None:
            raise ValueError("Mesh needed to be specified")

        # Confirm that the import file exists and import it, then import the desired method from it. Evaluate the method
        # using the current values of the new variables to obtain an initial value.
        sys.path.append(import_dir)
        import import_functions
        variable_eval = getattr(import_functions, import_name)
        val = variable_eval(t_param, new_variables, mesh)
        return val, variable_eval

    elif op[0].isalpha():
        return op, variable_eval

    else:
        # Try to evaluate as int first, then as float if int fails.
        try:
            return int(op), variable_eval
        except ValueError:
            return float(op), variable_eval


def eval_item(string: str, import_dir: str, t_param: Optional[Parameter], new_variables: Dict[str, Any], mesh: Optional[Mesh] = None) \
        -> Tuple[Union[str, float, CoefficientFunction], Union[str, bool, callable]]:
    """
    Parse a string containing a single expression (not a list of expressions).

    Args:
        string: The string of interest.
        import_dir: The path to the main run directory containing the file from which to import any Python functions.
        t_param: Parameter representing the current time.
        new_variables: A dictionary of any new model variables and their values.
        mesh: Mesh used by the model

    Returns:
        Tuple[Union[str, float, CoefficientFunction], Union[str, bool, callable]]:
            - val: The Python code.
            - variable_eval: Whether or not the expression contains any of the new model variables (would need to be
                re-parsed if their values change). If variable_eval is True, the original string expression is returned
                in its place. If the expression involved importing a Python function that Python function is returned in 
                place of variable_eval.
    """

    expr_stack = []
    # TODO: alex@elizabeth: Why is results not being used? What's going on here?
    results = parse_to_arith(expr_stack).parseString(string, parseAll=True)
    val, variable_eval = evaluate_arith_stack(expr_stack[:], import_dir, t_param, new_variables, mesh)

    if callable(variable_eval):
        return val, variable_eval
    elif variable_eval:
        return val, string
    else:
        return val, variable_eval


def eval_python(string: str, import_dir: str, mesh: Optional[Mesh] = None, new_variables: Dict[str, Any] = {},
                t_param: Optional[Parameter] = None) -> Tuple[Union[str, float, CoefficientFunction], Union[str, bool, callable]]:
    """
    Parses a string into Python code.

    Args:
        string: The string of interest.
        import_dir: The path to the main run directory containing the file from which to import any Python functions.
        mesh: Mesh used by the model
        t_param: Parameter representing the current time.
        new_variables: A dictionary of any new model variables and their values.

    Returns:
        Tuple[Union[str, float, CoefficientFunction], Union[str, bool, callable]]:
            - val: The Python code.
            - variable_eval: Whether or not the expression contains any of the new model variables (would need to be
                re-parsed if their values change). If variable_eval is True, the original string expression is returned
                in its place. If the expression involved importing a Python function that Python function is returned in 
                place of variable_eval.
    """

    # Remove whitespace in case the string ends up being parsed manually.
    string = string.replace(' ', '')

    # Set a flag to denote that the expression is a function of one or more of the model variables and would need to be
    # re-parsed when that variable's value changes.
    variable_eval = False

    if (string[0] == '<') and (string[-1] == '>') and (',' in string):
        # Coordinate or list of coordinates.
        string_lst = string.split('>,')

        val = []
        for item in string_lst:
            item_lst = item.split(',')
            item_lst = [float(iitem.strip('<> ')) for iitem in item_lst]
            val.append(tuple(item_lst))

        if len(val) == 1:
            # Only return as a list if there are multiple values.
            if variable_eval:
                return val[0], string
            else:
                return val[0], variable_eval

    elif ',[' in string or '],' in string:
        # List of vector expressions or mixed list of vector and scalar expressions.
        string_lst = string.replace(',[', ',[[').split(',[')
        val = []
        for item in string_lst:
            if '],' in item:
                iitem_lst = item.replace('],', ']],').split('],')
                for iitem in iitem_lst:
                    if not('[' in iitem) and not(']' in iitem) and ',' in iitem:
                        # Additional list of scalar expressions.
                        iiitem_lst = iitem.split(',')
                        for iiitem in iiitem_lst:
                            val_tmp, variable_eval_tmp = eval_item(iiitem, import_dir, t_param, new_variables, mesh)
                            val.append(val_tmp)
                            variable_eval = variable_eval or variable_eval_tmp
                    else:
                        val_tmp, variable_eval_tmp = eval_item(iitem, import_dir, t_param, new_variables, mesh)
                        val.append(val_tmp)
                        variable_eval = variable_eval or variable_eval_tmp

            elif not('[' in item) and not(']' in item) and ',' in item:
                # Additional list of scalar expressions.
                iitem_lst = item.split(',')
                for iitem in iitem_lst:
                    val_tmp, variable_eval_tmp = eval_item(iitem, import_dir, t_param, new_variables, mesh)
                    val.append(val_tmp)
                    variable_eval = variable_eval or variable_eval_tmp

            else:
                # Single vector or scalar expression.
                val_tmp, variable_eval_tmp = eval_item(item, import_dir, t_param, new_variables, mesh)
                val.append(val_tmp)
                variable_eval = variable_eval or variable_eval_tmp

    else:
        # Catches the error eval_item will throw when it encounters a comma that would indicate a list of
        # scalar expressions.
        try:
            val, variable_eval_tmp = eval_item(string, import_dir, t_param, new_variables, mesh)
            variable_eval = variable_eval or variable_eval_tmp
        except:
            item_lst = string.split(',')
            val = []
            for item in item_lst:
                val_tmp, variable_eval_tmp = eval_item(item, import_dir, t_param, new_variables, mesh)
                val.append(val_tmp)
                variable_eval = variable_eval or variable_eval_tmp

    if callable(variable_eval):
        return val, variable_eval
    elif variable_eval:
        return val, string
    else:
        return val, variable_eval
