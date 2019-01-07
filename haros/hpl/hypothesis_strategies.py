
#Copyright (c) 2018 Andre Santos
#
#Permission is hereby granted, free of charge, to any person obtaining a copy
#of this software and associated documentation files (the "Software"), to deal
#in the Software without restriction, including without limitation the rights
#to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#copies of the Software, and to permit persons to whom the Software is
#furnished to do so, subject to the following conditions:

#The above copyright notice and this permission notice shall be included in
#all copies or substantial portions of the Software.

#THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
#THE SOFTWARE.


################################################################################
# Imports
################################################################################

import itertools

from .ros_types import (
    INT8_MIN_VALUE, INT8_MAX_VALUE, INT16_MIN_VALUE, INT16_MAX_VALUE,
    INT32_MIN_VALUE, INT32_MAX_VALUE, INT64_MIN_VALUE, INT64_MAX_VALUE,
    UINT8_MAX_VALUE, UINT16_MAX_VALUE, UINT32_MAX_VALUE, UINT64_MAX_VALUE,
    FLOAT32_MIN_VALUE, FLOAT32_MAX_VALUE, FLOAT64_MIN_VALUE, FLOAT64_MAX_VALUE,
    TypeToken, ArrayTypeToken, ROS_BUILTIN_TYPES, ROS_INT_TYPES,
    ROS_FLOAT_TYPES, ROS_BOOLEAN_TYPES, ROS_STRING_TYPES, ROS_NUMBER_TYPES,
    ROS_PRIMITIVE_TYPES
)


################################################################################
# Strategy Map
################################################################################

class StrategyMap(object):
    __slots__ = ("msg_data", "defaults", "custom")

    def __init__(self, msg_data):
        # msg_data :: {str(msg_type): {str(field_name): TypeToken}}
        # assume msg_data contains all dependencies
        if (not isinstance(msg_data, dict)
                or not all(isinstance(v, dict) for v in msg_data.itervalues())):
            raise TypeError("expected dict: {msg: {field: type}}")
        self.msg_data = msg_data
        # defaults :: {str(ros_type): TopLevelStrategy}
        self.defaults = {}
        # custom :: {str(group): {str(msg_type): MsgStrategy}}
        self.custom = {}
        self._make_builtins()
        self._make_defaults()

    def get_custom(self, group, msg_type):
        return self.custom[group][msg_type]

    def make_custom(self, group, msg_type):
        custom = self.custom.get(group)
        if not custom:
            custom = {}
            self.custom[group] = custom
        if not msg_type in self.msg_data:
            raise ValueError("'{}' is not defined".format(msg_type))
        type_data = self.msg_data[msg_type]
        if msg_type in custom:
            raise ValueError("'{}' is already defined in '{}'".format(
                msg_type, group))
        name = "{}_{}".format(group, msg_type.replace("/", "_"))
        strategy = MsgStrategy(msg_type, name=name)
        custom[msg_type] = strategy
        return strategy

    def make_custom_tree(self, group, msg_type):
        custom = self.custom.get(group)
        if not custom:
            custom = {}
            self.custom[group] = custom
        queue = [msg_type]
        while queue:
            current_type = queue.pop(0)
            if not current_type in self.msg_data:
                raise ValueError("'{}' is not defined".format(current_type))
            type_data = self.msg_data[current_type]
            if current_type in custom:
                raise ValueError("'{}' is already defined in '{}'".format(
                    current_type, group))
            name = "{}_{}".format(group, current_type.replace("/", "_"))
            strategy = MsgStrategy(current_type, name=name)
            custom[current_type] = strategy
            for type_token in type_data.itervalues():
                if not type_token.ros_type in ROS_BUILTIN_TYPES:
                    queue.append(type_token.ros_type)
        return custom[msg_type]

    def complete_custom_strategies(self):
        for strategies in self.custom.itervalues():
            for msg_type, strategy in strategies.iteritems():
                default = self.defaults[msg_type]
                for field_name, field_strategy in default.fields.iteritems():
                    if not field_name in strategy.fields:
                        strategy.fields[field_name] = field_strategy

    def _make_builtins(self):
        self.defaults["bool"] = RosBoolStrategy()
        self.defaults["string"] = RosStringStrategy()
        self.defaults["time"] = RosTimeStrategy()
        self.defaults["duration"] = RosDurationStrategy()
        self.defaults["std_msgs/Header"] = HeaderStrategy()
        for ros_type in RosIntStrategy.TYPES:
            self.defaults[ros_type] = RosIntStrategy(ros_type)
        for ros_type in RosFloatStrategy.TYPES:
            self.defaults[ros_type] = RosFloatStrategy(ros_type)

    def _make_defaults(self):
        for msg_type, data in self.msg_data.iteritems():
            strategy = MsgStrategy(msg_type)
            self.defaults[msg_type] = strategy
            for field_name, type_token in data.iteritems():
                strategy.fields[field_name] = FieldStrategy.make_default(
                    field_name, type_token)


################################################################################
# Top-level Strategies
################################################################################

class TopLevelStrategy(object):
    @property
    def name(self):
        raise NotImplementedError("subclasses must override this property")

    def to_python(self, var_name="v", module="strategies",
                  indent=0, tab_size=4):
        raise NotImplementedError("subclasses must override this method")


class RosBuiltinStrategy(TopLevelStrategy):
    __slots__ = ("ros_type",)

    TYPES = ()

    def __init__(self, ros_type):
        if not ros_type in self.TYPES:
            raise ValueError("unknown built-in type: {}".format(ros_type))
        self.ros_type = ros_type

    @property
    def name(self):
        return "ros_" + self.ros_type


class DefaultMsgStrategy(TopLevelStrategy):
    TMP = ("{indent}@{module}.composite\n"
           "{indent}def {name}(draw):\n"
           "{indent}{tab}{var} = {pkg}.{msg}()\n"
           "{definition}\n"
           "{indent}{tab}return {var}")

    __slots__ = ("msg_type", "fields")

    def __init__(self, msg_type, msg_data):
        # msg_data :: {str(field): TypeToken}
        self.msg_type = msg_type
        self.fields = {
            field_name: FieldStrategy.make_default(field_name, type_token)
            for field_name, type_token in msg_data.iteritems()
        }

    @property
    def name(self):
        return ros_type_to_name(self.msg_type)

    def to_python(self, var_name="msg", module="strategies",
                  indent=0, tab_size=4):
        assert "/" in self.msg_type
        pkg, msg = self.msg_type.split("/")
        ws = " " * indent
        mws = " " * tab_size
        body = "\n".join(f.to_python(var_name=var_name,
                                     module=module,
                                     indent=(indent + tab_size),
                                     tab_size=tab_size)
                         for f in self.fields.itervalues())
        return self.TMP.format(indent=ws, tab=mws, pkg=pkg, msg=msg,
                               name=self._name, var=var_name,
                               definition=body, module=module)


class MsgStrategy(TopLevelStrategy):
    TMP = ("{indent}@{module}.composite\n"
           "{indent}def {name}(draw):\n"
           "{indent}{tab}{var} = {pkg}.{msg}()\n"
           "{definition}\n"
           "{indent}{tab}return {var}")

    __slots__ = ("msg_type", "fields", "_name", "_done")

    def __init__(self, msg_type, name=None):
        self.msg_type = msg_type
        self.fields = {}
        self._name = name if name else ros_type_to_name(msg_type)
        self._done = None

    @property
    def name(self):
        return self._name

    # build sub field tree based on msg_data
    # add conditions to fields as needed
    # traverse field tree to build a list of lines of code
    # mark each sub field as completely generated or not
    # generate defaults and literals on first iteration
    # references that cannot be resolved throw an exception
    # main loop catches exception and enqueues field for next iteration
    # whenever queue stays the same size from one iteration to another,
    # a cyclic dependency has been detected


    # def strategy(draw):
    #   msg = Msg()
    #   select = Selector(msg) ?
    #   ---------------------- build the skeleton
    #   msg.a = draw(default_composite())
    #   msg.b = Composite()
    #   msg.b.a = 1
    #   ---------------------- start with array of None
    #   msg.c = draw(lists(min_size=3, max_size=3))     # [None, None, None]
    #   reserved = (1,)
    #   ---------------------- build 'some' fields avoiding fixed indices
    #   assume(len(msg.c) - len(reserved) >= #some)
    #   msg.c[0] = 1    # msg.c[some] = 1
    #   ---------------------- build default fields to fill up array
    #   for i in xrange(#some, len(msg.c)):
    #       if not i in reserved:
    #           msg.c[i] = draw(default_value())
    #   

    def to_python(self, var_name="msg", module="strategies",
                  indent=0, tab_size=4):
        assert "/" in self.msg_type
        pkg, msg = self.msg_type.split("/")
        ws = " " * indent
        mws = " " * tab_size
        self._done = []
        body = []
        self._init_fields(body, indent, tab_size)
        self._field_assumptions(body, indent, tab_size)
        body = "\n".join(body)
        self._done = None
        return self.TMP.format(indent=ws, tab=mws, pkg=pkg, msg=msg,
                               name=self._name, var=var_name,
                               definition=body, module=module)

    def _init_fields(self, body, indent, tab_size):
        assert not self._done is None
        queue = list(self.fields.itervalues())
        while queue:
            n = len(self._done)
            new_queue = []
            for field in queue:
                try:
                    body.append(field.to_python(module=module,
                        indent=(indent + tab_size), tab_size=tab_size))
                    self._done.append(field)
                    new_queue.extend(field.children())
                except ResolutionError as e:
                    new_queue.append(field)
            queue = new_queue
            if n == len(self._done):
                raise CyclicDependencyError(repr(queue))

    def _field_assumptions(self, body, indent, tab_size):
        assert not self._done is None
        queue = list(self.fields.itervalues())
        while queue:
            n = len(self._done)
            new_queue = []
            for field in queue:
                try:
                    body.append(field.assumptions(
                        indent=(indent + tab_size), tab_size=tab_size))
                    new_queue.extend(field.children())
                except ResolutionError as e:
                    new_queue.append(field)
            queue = new_queue
            if n == len(self._done):
                raise CyclicDependencyError(repr(queue))


class CyclicDependencyError(Exception):
    pass


################################################################################
# Built-in Strategies
################################################################################

class RosBoolStrategy(RosBuiltinStrategy):
    TYPES = ("bool",)

    TMP = ("{indent}def ros_bool():\n"
           "{indent}{tab}return {module}.booleans()")

    def __init__(self):
        RosBuiltinStrategy.__init__(self, "bool")

    @classmethod
    def accepts(cls, ros_type, value):
        if ros_type in cls.TYPES:
            raise ValueError("invalid ROS type: " + repr(ros_type))
        if isinstance(value, Selector):
            return value.ros_type == ros_type
        if isinstance(value, int):
            return value == 0 or value == 1
        if not isinstance(value, bool):
            raise TypeError("expected a bool value: " + repr(value))
        return True

    def to_python(self, var_name="v", module="strategies",
                  indent=0, tab_size=4):
        ws = " " * indent
        mws = " " * tab_size
        return self.TMP.format(indent=ws, tab=mws, module=module)


class RosIntStrategy(RosBuiltinStrategy):
    TYPES = {
        "char": (0, UINT8_MAX_VALUE),
        "uint8": (0, UINT8_MAX_VALUE),
        "byte": (INT8_MIN_VALUE, INT8_MAX_VALUE),
        "int8": (INT8_MIN_VALUE, INT8_MAX_VALUE),
        "uint16": (0, UINT16_MAX_VALUE),
        "int16": (INT16_MIN_VALUE, INT16_MAX_VALUE),
        "uint32": (0, UINT32_MAX_VALUE),
        "int32": (INT32_MIN_VALUE, INT32_MAX_VALUE),
        "uint64": (0, UINT64_MAX_VALUE),
        "int64": (INT64_MIN_VALUE, INT64_MAX_VALUE)
    }

    TMP = ("{indent}def ros_{ros_type}(min_value={min_value}, "
           "max_value={max_value}):\n"
           "{indent}{tab}if min_value <= {min_value} "
           "or min_value >= {max_value} "
           "or max_value <= {min_value} "
           "or max_value >= {max_value} "
           "or min_value > max_value:\n"
           "{indent}{tab}{tab}"
           "raise ValueError('values out of bounds: {{}}, {{}}'"
           ".format(min_value, max_value))\n"
           "{indent}{tab}return {module}.integers("
           "min_value=max(min_value, {min_value}), "
           "max_value=min(max_value, {max_value}))")

    @classmethod
    def accepts(cls, ros_type, value):
        if ros_type in cls.TYPES:
            raise ValueError("invalid ROS type: " + repr(ros_type))
        if isinstance(value, Selector):
            return value.ros_type == ros_type
        if not isinstance(value, (int, long)):
            raise TypeError("expected an int value: " + repr(value))
        min_value, max_value = cls.TYPES[ros_type]
        return value >= min_value and value <= max_value

    def to_python(self, var_name="v", module="strategies",
                  indent=0, tab_size=4):
        ws = " " * indent
        mws = " " * tab_size
        minv, maxv = self.TYPES[self.ros_type]
        return self.TMP.format(indent=ws, ros_type=self.ros_type,
            min_value=minv, max_value=maxv, tab=mws, module=module)


class RosFloatStrategy(RosBuiltinStrategy):
    TYPES = {
        "float32": (FLOAT32_MIN_VALUE, FLOAT32_MAX_VALUE, 32),
        "float64": (FLOAT64_MIN_VALUE, FLOAT64_MAX_VALUE, 64)
    }

    TMP = ("{indent}def ros_{ros_type}(min_value={min_value}, "
           "max_value={max_value}):\n"
           "{indent}{tab}if min_value <= {min_value} "
           "or min_value >= {max_value} "
           "or max_value <= {min_value} "
           "or max_value >= {max_value} "
           "or min_value > max_value:\n"
           "{indent}{tab}{tab}"
           "raise ValueError('values out of bounds: {{}}, {{}}'"
           ".format(min_value, max_value))\n"
           "{indent}{tab}return {module}.floats("
           "min_value=max(min_value, {min_value}), "
           "max_value=min(max_value, {max_value}), "
           "width={width})")

    @classmethod
    def accepts(cls, ros_type, value):
        if ros_type in cls.TYPES:
            raise ValueError("invalid ROS type: " + repr(ros_type))
        if isinstance(value, Selector):
            return value.ros_type == ros_type
        if not isinstance(value, float):
            raise TypeError("expected a float value: " + repr(value))
        min_value, max_value, width = cls.TYPES[ros_type]
        return value >= min_value and value <= max_value

    def to_python(self, var_name="v", module="strategies",
                  indent=0, tab_size=4):
        ws = " " * indent
        mws = " " * tab_size
        minv, maxv, width = self.TYPES[self.ros_type]
        return self.TMP.format(indent=ws, ros_type=self.ros_type, width=width,
            min_value=minv, max_value=maxv, tab=mws, module=module)


class RosStringStrategy(RosBuiltinStrategy):
    TYPES = ("string",)

    TMP = ("{indent}def ros_string():\n"
           "{indent}{tab}return {module}.binary("
           "min_size=0, max_size=256)")

    def __init__(self):
        RosBuiltinStrategy.__init__(self, "string")

    @classmethod
    def accepts(cls, ros_type, value):
        if ros_type in cls.TYPES:
            raise ValueError("invalid ROS type: " + repr(ros_type))
        if isinstance(value, Selector):
            return value.ros_type == ros_type
        if not isinstance(value, basestring):
            raise TypeError("expected a string value: " + repr(value))
        return True

    def to_python(self, var_name="v", module="strategies",
                  indent=0, tab_size=4):
        ws = " " * indent
        mws = " " * tab_size
        return self.TMP.format(indent=ws, tab=mws, module=module)


# import rospy
class RosTimeStrategy(RosBuiltinStrategy):
    TYPES = ("time",)

    TMP = ("{indent}@{module}.composite\n"
           "{indent}def ros_time(draw):\n"
           "{indent}{tab}secs = draw({module}.integers("
           "min_value=0, max_value=4294967295))\n"
           "{indent}{tab}nsecs = draw({module}.integers("
           "min_value=0, max_value=4294967295))\n"
           "{indent}{tab}return rospy.Time(secs, nsecs)")

    def __init__(self):
        RosBuiltinStrategy.__init__(self, "time")

    def to_python(self, var_name="v", module="strategies",
                  indent=0, tab_size=4):
        ws = " " * indent
        mws = " " * tab_size
        return self.TMP.format(indent=ws, tab=mws, module=module)


# import rospy
class RosDurationStrategy(RosBuiltinStrategy):
    TYPES = ("duration",)

    TMP = ("{indent}@{module}.composite\n"
           "{indent}def ros_duration(draw):\n"
           "{indent}{tab}secs = draw({module}.integers("
           "min_value=-2147483648, max_value=2147483647))\n"
           "{indent}{tab}nsecs = draw({module}.integers("
           "min_value=-2147483648, max_value=2147483647))\n"
           "{indent}{tab}return rospy.Duration(secs, nsecs)")

    def __init__(self):
        RosBuiltinStrategy.__init__(self, "duration")

    def to_python(self, var_name="v", module="strategies",
                  indent=0, tab_size=4):
        ws = " " * indent
        mws = " " * tab_size
        return self.TMP.format(indent=ws, tab=mws, module=module)


# import std_msgs.msg as std_msgs
class HeaderStrategy(RosBuiltinStrategy):
    TYPES = ("std_msgs/Header", "Header")

    TMP = ("{indent}@{module}.composite\n"
           "{indent}def std_msgs_Header(draw):\n"
           "{indent}{tab}msg = std_msgs.Header()\n"
           "{indent}{tab}msg.stamp = draw(ros_time())\n"
           "{indent}{tab}msg.frame_id = draw(ros_string())\n"
           "{indent}{tab}return msg")

    def __init__(self):
        RosBuiltinStrategy.__init__(self, "std_msgs/Header")

    @property
    def name(self):
        return "std_msgs_Header"

    def to_python(self, var_name="v", module="strategies",
                  indent=0, tab_size=4):
        ws = " " * indent
        mws = " " * tab_size
        return self.TMP.format(indent=ws, tab=mws, module=module)


################################################################################
# Message Field Generators
################################################################################

class ResolutionError(Exception):
    pass


class Selector(object):
    ALL = object()  # pill
    SOME = object() # pill

    # selects field from root message for references
    def __init__(self, root, fields, ros_type):
        self.root = root
        self.fields = fields
        self.ros_type = ros_type

    def to_python(self):
        values = []
        as_list = False
        field = self.root
        for name in self.fields:
            if name is self.ALL:
                as_list = True
                field = self.MultiField(field.all())
            elif name is self.SOME:
                field = field.some()
            else:
                field = field.fields[name]
            if not field.generated:
                raise ResolutionError(field.full_name)
        assert not isinstance(field, ArrayGenerator)
        if as_list:
            assert isinstance(field, self.MultiField)
            for f in field._fields:
                if not 
        if not isinstance(field, SimpleFieldGenerator)


    class MultiField(object):
        def __init__(self, fields):
            self._fields = fields

        @property
        def full_name(self):
            return repr(tuple(f.full_name for f in self._fields))

        @property
        def fields(self):
            return self

        @property
        def generated(self):
            return all(f.generated for f in self._fields)

        def all(self):
            assert all(isinstance(f, ArrayGenerator) for f in self._fields)
            return tuple(itertools.chain.from_iterable(
                f.fields for f in self._fields))

        def some(self):
            assert all(isinstance(f, ArrayGenerator) for f in self._fields)
            return tuple(f.some() for f in self._fields)

        def __getitem__(self, key):
            return Selector.MultiField(
                tuple(f.fields[key] for f in self._fields))


VALUE_TYPES = (bool, int, long, float, basestring, Selector)


class InconsistencyError(Exception):
    pass


class UnsupportedOperationError(Exception):
    pass


# TODO add assume on list length when a selector for fixed index is created


class BaseGenerator(object):
    __slots__ = ("parent", "field_name", "ros_type", "generated")

    TMP = "{indent}{field} = {strategy}"

    def __init__(self, parent, field_name, ros_type):
        self.parent = parent
        self.field_name = field_name
        self.ros_type = ros_type
        self.generated = False

    @property
    def full_name(self):
        return self.parent.full_name + "." + self.field_name

    @property
    def is_default(self):
        raise NotImplementedError("subclasses must implement this property")

    def children(self):
        raise NotImplementedError("subclasses must implement this method")

    def eq(self, value):
        raise NotImplementedError("subclasses must implement this method")

    def neq(self, value):
        raise NotImplementedError("subclasses must implement this method")

    def lt(self, value):
        raise NotImplementedError("subclasses must implement this method")

    def lte(self, value):
        raise NotImplementedError("subclasses must implement this method")

    def gt(self, value):
        raise NotImplementedError("subclasses must implement this method")

    def gte(self, value):
        raise NotImplementedError("subclasses must implement this method")

    def in_set(self, values):
        raise NotImplementedError("subclasses must implement this method")

    def not_in(self, values):
        raise NotImplementedError("subclasses must implement this method")

    def to_python(self, module="strategies", indent=0, tab_size=4):
        raise NotImplementedError("subclasses must implement this method")

    def assumptions(self, indent=0, tab_size=4):
        raise NotImplementedError("subclasses must implement this method")

    def tree_to_python(self, module="strategies", indent=0, tab_size=4):
        raise NotImplementedError("subclasses must implement this method")


class FieldGenerator(BaseGenerator):
    __slots__ = BaseGenerator.__slots__ + ("index",)

    def __init__(self, parent, field_name, ros_type, index=None):
        BaseGenerator.__init__(self, parent, field_name, ros_type)
        self.index = index

    @property
    def full_name(self):
        return self.parent.full_name + "." + self.indexed_name

    @property
    def indexed_name(self):
        if not self.index is None:
            return "{}[{}]".format(self.field_name, self.index)
        return self.field_name

    def copy(self, parent, field_name, deep=False):
        raise NotImplementedError("subclasses must implement this method")


class SimpleFieldGenerator(FieldGenerator):
    __slots__ = FieldGenerator.__slots__ + ("condition", "constant", "pool")

    POOL = "draw({module}.sampled_from({values}))"

    DEFAULT = "draw({strategy}())"

    def __init__(self, parent, field_name, ros_type, index=None):
        FieldGenerator.__init__(self, parent, field_name, ros_type, index=index)
        self.condition = None
        self.constant = None
        self.pool = None

    @property
    def is_default(self):
        return (self.constant is None and self.pool is None
                and self.condition is None)

    def children(self):
        return ()

    def eq(self, value):
        self._type_check_value(value)
        if not self.constant is None or not self.pool is None:
            raise InconsistencyError()
        self.constant = value
        self._set_condition(EqualsCondition(value))

    def neq(self, value):
        self._type_check_value(value)
        self._set_condition(NotEqualsCondition(value))

    def lt(self, value):
        raise UnsupportedOperationError()

    def lte(self, value):
        raise UnsupportedOperationError()

    def gt(self, value):
        raise UnsupportedOperationError()

    def gte(self, value):
        raise UnsupportedOperationError()

    def in_set(self, values):
        if not isinstance(values, tuple) or isinstance(values, list):
            raise TypeError("expected collection of values: " + repr(values))
        for value in values:
            self._type_check_value(value)
        if not self.constant is None or not self.pool is None:
            raise InconsistencyError()
        self.pool = values
        self._set_condition(InCondition(values))

    def not_in(self, values):
        if not isinstance(values, tuple) or isinstance(values, list):
            raise TypeError("expected collection of values: " + repr(values))
        for value in values:
            self._type_check_value(value)
        self._set_condition(NotInCondition(values))

    def to_python(self, module="strategies", indent=0, tab_size=4):
        assert not self.generated
        if not self.constant is None:
            strategy = value_to_python(self.constant)
        elif not self.pool is None:
            strategy = self.POOL.format(module=module,
                values=value_to_python(self.pool))
        else:
            strategy = self.DEFAULT.format(
                strategy=ros_type_to_name(self.ros_type))
        ws = " " * indent
        self.generated = True
        return self.TMP.format(indent=ws, field=self.full_name,
                               strategy=strategy)

    def assumptions(self, indent=0, tab_size=4):
        if self.condition is None:
            return None
        return self.condition.to_python(
            self.full_name, indent=indent, tab_size=tab_size)

    def tree_to_python(self, module="strategies", indent=0, tab_size=4):
        assert not self.generated
        init = self.to_python(module=module, indent=indent, tab_size=tab_size)
        assumptions = self.assumptions(indent=indent, tab_size=tab_size)
        self.generated = True
        if assumptions:
            return init + "\n" + assumptions
        return init

    def copy(self, parent, field_name, index=None, deep=False):
        new = SimpleFieldGenerator(parent, field_name,
                                   self.ros_type, index=index)
        if deep:
            new.condition = self.condition
            new.constant = self.constant
            new.pool = self.pool
        return new

    def _type_check_value(self, value):
        if (self.ros_type in ROS_INT_TYPES
                and not RosIntStrategy.accepts(self.ros_type, value)):
            raise ValueError("invalid int value: " + repr(value))
        if (self.ros_type in ROS_FLOAT_TYPES
                and not RosFloatStrategy.accepts(self.ros_type, value)):
            raise ValueError("invalid float value: " + repr(value))
        if (self.ros_type in ROS_BOOLEAN_TYPES
                and not RosBoolStrategy.accepts(self.ros_type, value)):
            raise ValueError("invalid bool value: " + repr(value))
        if (self.ros_type in ROS_STRING_TYPES
                and not RosStringStrategy.accepts(self.ros_type, value)):
            raise ValueError("invalid string value: " + repr(value))

    def _set_condition(self, condition):
        if self.condition is None:
            self.condition = condition
        else:
            self.condition = self.condition.merge(condition)


class NumericFieldGenerator(SimpleFieldGenerator):
    __slots__ = SimpleFieldGenerator.__slots__ + ("min_value", "max_value")

    DEFAULT = "draw({strategy}({args}))"

    def __init__(self, parent, field_name, ros_type, index=None):
        assert ros_type in ROS_NUMBER_TYPES
        SimpleFieldGenerator.__init__(self, parent, field_name,
                                      ros_type, index=index)
        self.min_value = None
        self.max_value = None

    def lt(self, value):
        self._type_check_value(value)
        self.max_value = value
        self._set_condition(LessThanCondition(value, strict=True))

    def lte(self, value):
        self._type_check_value(value)
        self.max_value = value
        self._set_condition(LessThanCondition(value, strict=False))

    def gt(self, value):
        self._type_check_value(value)
        self.min_value = value
        self._set_condition(GreaterThanCondition(value, strict=True))

    def gte(self, value):
        self._type_check_value(value)
        self.min_value = value
        self._set_condition(GreaterThanCondition(value, strict=False))

    def to_python(self, module="strategies", indent=0, tab_size=4):
        assert not self.generated
        if not self.constant is None:
            strategy = value_to_python(self.constant)
        elif not self.pool is None:
            strategy = self.POOL.format(module=module,
                values=value_to_python(self.pool))
        else:
            args = []
            if not self.min_value is None:
                args.append("min_value=" + value_to_python(self.min_value))
            if not self.max_value is None:
                args.append("max_value=" + value_to_python(self.max_value))
            args = ", ".join(args)
            strategy = "draw({}({}))".format(
                ros_type_to_name(self.ros_type), args)
        ws = " " * indent
        self.generated = True
        return self.TMP.format(indent=ws, field=self.full_name,
                               strategy=strategy)

    def copy(self, parent, field_name, index=None, deep=False):
        new = NumericFieldGenerator(parent, field_name,
                                    self.ros_type, index=index)
        if deep:
            new.condition = self.condition
            new.constant = self.constant
            new.pool = self.pool
            new.min_value = self.min_value
            new.max_value = self.max_value
        return new


class CompositeFieldGenerator(FieldGenerator):
    __slots__ = FieldGenerator.__slots__ + ("fields",)

    DEFAULT = "draw({strategy}())"

    CUSTOM = "{pkg}.{msg}()"

    def __init__(self, parent, field_name, ros_type, index=None):
        FieldGenerator.__init__(self, parent, field_name, ros_type, index=index)
        self.fields = {}

    @property
    def is_default(self):
        for field in self.fields.itervalues():
            if not field.is_default:
                return False
        return True

    def children(self):
        return list(self.fields.itervalues())

    def eq(self, value):
        raise UnsupportedOperationError()

    def neq(self, value):
        raise UnsupportedOperationError()

    def lt(self, value):
        raise UnsupportedOperationError()

    def lte(self, value):
        raise UnsupportedOperationError()

    def gt(self, value):
        raise UnsupportedOperationError()

    def gte(self, value):
        raise UnsupportedOperationError()

    def in_set(self, values):
        raise UnsupportedOperationError()

    def not_in(self, values):
        raise UnsupportedOperationError()

    def to_python(self, module="strategies", indent=0, tab_size=4):
        assert not self.generated
        if self.is_default:
            strategy = self.DEFAULT.format(
                strategy=ros_type_to_name(self.ros_type))
        else:
            pkg, msg = self.ros_type.split("/")
            strategy = self.CUSTOM.format(pkg=pkg, msg=msg)
        ws = " " * indent
        self.generated = True
        return self.TMP.format(
            indent=ws, field=self.full_name, strategy=strategy)

    def assumptions(self, indent=0, tab_size=4):
        return None

    def tree_to_python(self, module="strategies", indent=0, tab_size=4):
        assert not self.generated
        code = [self.to_python(module=module, indent=indent, tab_size=tab_size)]
        for field in self.fields.itervalues():
            code.append(field.tree_to_python(
                module=module, indent=indent, tab_size=tab_size))
        self.generated = True
        return "\n".join(code)

    def copy(self, parent, field_name, index=None, deep=False):
        new = CompositeFieldGenerator(parent, field_name,
                                      self.ros_type, index=index)
        for name, field in self.fields.iteritems():
            new.fields[name] = field.copy(new, name, deep=deep)
        return new


class ArrayGenerator(BaseGenerator):
    def some(self):
        raise NotImplementedError("subclasses must implement this property")

    def all(self):
        raise NotImplementedError("subclasses must implement this method")


class FixedLengthArrayGenerator(ArrayGenerator):
    __slots__ = ArrayGenerator.__slots__ + ("length", "fields", "_some")

    TMP = ("{indent}{field} = draw({module}.lists("
           "min_size={length}, max_size={length}))")

    def __init__(self, parent, field_name, ros_type, length, default_field):
        if not isinstance(default_field, FieldGenerator):
            raise TypeError("unexpected field: " + repr(default_field))
        ArrayGenerator.__init__(self, parent, field_name, ros_type)
        self.length = length
        self.fields = [default_field.copy(parent, field_name, index=i)
                       for i in xrange(length)]
        self._some = None

    @property
    def is_default(self):
        return all(f.is_default for f in self.fields)

    def some(self):
        if not self.length:
            raise UnsupportedOperationError()
        if self._some is None:
            i = "random_index(draw, {})".format(self.full_name)
            new = self.fields[0].copy(self.parent, self.field_name,
                                      index=i)
            self._some = new
        return self._some

    def all(self):
        return self.fields

    def children(self):
        return self.fields

    def eq(self, value):
        for field in self.fields:
            field.eq(value)

    def neq(self, value):
        for field in self.fields:
            field.neq(value)

    def lt(self, value):
        for field in self.fields:
            field.lt(value)

    def lte(self, value):
        for field in self.fields:
            field.lte(value)

    def gt(self, value):
        for field in self.fields:
            field.gt(value)

    def gte(self, value):
        for field in self.fields:
            field.gte(value)

    def in_set(self, values):
        for field in self.fields:
            field.in_set(values)

    def not_in(self, values):
        for field in self.fields:
            field.not_in(values)

    # TODO byte arrays
    def to_python(self, module="strategies", indent=0, tab_size=4):
        assert not self.generated
        ws = " " * indent
        self._assign_some()
        self.generated = True
        return self.TMP.format(indent=ws, field=self.full_name,
            module=module, length=self.length)

    def assumptions(self, indent=0, tab_size=4):
        return None

    def tree_to_python(self, module="strategies", indent=0, tab_size=4):
        assert not self.generated
        code = [self.to_python(module=module, indent=indent, tab_size=tab_size)]
        for field in self.fields:
            code.append(field.tree_to_python(
                module=module, indent=indent, tab_size=tab_size))
        self.generated = True
        return "\n".join(code)

    def _assign_some(self):
        available = tuple(i for i in xrange(len(self.fields))
                          if self.fields[i].is_default)
        if not available:
            raise InconsistencyError()
        self._some.index = available[0]
        self.fields[available[0]] = self._some
        self._some = None


class VariableLengthArrayGenerator(ArrayGenerator):
    __slots__ = ArrayGenerator.__slots__ + ("_all", "fields")

    TMP = ("{indent}{field} = draw({module}.lists(min_size=0, max_size=256))\n"
           "{indent}for i in xrange(len({field})):\n{strategy}")

    ASSUMES = "{indent}for i in xrange(len({field})):\n{condition}"

    def __init__(self, parent, field_name, ros_type, default_field):
        if not isinstance(default_field, FieldGenerator):
            raise TypeError("unexpected field: " + repr(default_field))
        ArrayGenerator.__init__(self, parent, field_name, ros_type)
        self._all = default_field.copy(parent, field_name, index="i")
        self.fields = () # meant to produce an IndexError

    @property
    def is_default(self):
        return self._all.is_default

    def some(self):
        raise UnsupportedOperationError()

    def all(self):
        return (self._all,)

    def children(self):
        return ()

    def eq(self, value):
        self._all.eq(value)

    def neq(self, value):
        self._all.neq(value)

    def lt(self, value):
        self._all.lt(value)

    def lte(self, value):
        self._all.lte(value)

    def gt(self, value):
        self._all.gt(value)

    def gte(self, value):
        self._all.gte(value)

    def in_set(self, values):
        self._all.in_set(values)

    def not_in(self, values):
        self._all.not_in(values)

    # TODO byte arrays
    def to_python(self, module="strategies", indent=0, tab_size=4):
        assert not self.generated
        # generate the whole sub tree here because of dynamic length,
        # as this requires indexing and iteration for all sub-sub-fields
        ws = " " * indent
        strategy = self._all.tree_to_python(
            module=module, indent=(indent + tab_size), tab_size=tab_size)
        self.generated = True
        return self.TMP.format(indent=ws, field=self.full_name, module=module,
                               strategy=strategy)

    def assumptions(self, indent=0, tab_size=4):
        return None

    def tree_to_python(self, module="strategies", indent=0, tab_size=4):
        assert not self.generated
        self.generated = True
        return self.to_python(module=module, indent=indent, tab_size=tab_size)



################################################################################
# Field Conditions
################################################################################

class Condition(object):
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value

    def merge(self, other):
        raise NotImplementedError("cannot merge conditions on the same field")

    def to_python(self, field_name, module="strategies", indent=0, tab_size=4):
        raise NotImplementedError("subclasses must implement this method")


class EqualsCondition(Condition):
    TMP = "{indent}assume({field} == {value})"

    def to_python(self, field_name, module="strategies", indent=0, tab_size=4):
        ws = " " * indent
        return self.TMP.format(indent=ws, field=field_name,
                               value=value_to_python(self.value))


class NotEqualsCondition(Condition):
    TMP = "{indent}assume({field} != {value})"

    def merge(self, other):
        if isinstance(other, NotEqualsCondition):
            values = (self.value, other.value)
            return NotInCondition(values)
        raise NotImplementedError("cannot merge conditions on the same field")

    def to_python(self, field_name, module="strategies", indent=0, tab_size=4):
        ws = " " * indent
        return self.TMP.format(indent=ws, field=field_name,
                               value=value_to_python(self.value))


class LessThanCondition(Condition):
    __slots__ = Condition.__slots__ + ("strict",)

    LT = "{indent}assume({field} < {value})"
    LTE = "{indent}assume({field} <= {value})"

    def __init__(self, value, strict=False):
        Condition.__init__(self, value)
        self.strict = strict

    def to_python(self, field_name, module="strategies", indent=0, tab_size=4):
        ws = " " * indent
        if self.strict:
            return self.LT.format(indent=ws, field=field_name,
                                  value=value_to_python(self.value))
        else:
            return self.LTE.format(indent=ws, field=field_name,
                                   value=value_to_python(self.value))


class GreaterThanCondition(Condition):
    __slots__ = Condition.__slots__ + ("strict",)

    GT = "{indent}assume({field} > {value})"
    GTE = "{indent}assume({field} >= {value})"

    def __init__(self, value, strict=False):
        Condition.__init__(self, value)
        self.strict = strict

    def to_python(self, field_name, module="strategies", indent=0, tab_size=4):
        ws = " " * indent
        if self.strict:
            return self.GT.format(indent=ws, field=field_name,
                                  value=value_to_python(self.value))
        else:
            return self.GTE.format(indent=ws, field=field_name,
                                   value=value_to_python(self.value))

class InCondition(Condition):
    TMP = "{indent}assume({field} in {values})"

    def to_python(self, field_name, module="strategies", indent=0, tab_size=4):
        assert isinstance(self.value, tuple) or isinstance(self.value, list)
        ws = " " * indent
        return self.TMP.format(indent=ws, field=field_name, module=module,
                               values=value_to_python(self.value))


class NotInCondition(Condition):
    TMP = "{indent}assume({excluded})"
    INNER = "{field} != {value}"

    def to_python(self, field_name, module="strategies", indent=0, tab_size=4):
        assert isinstance(self.value, tuple) or isinstance(self.value, list)
        ws = " " * indent
        inner = " and ".join(self.INNER.format(field=field_name,
            value=value_to_python(v)) for v in self.value)
        return self.TMP.format(indent=ws, excluded=inner)


################################################################################
# Strategies
################################################################################

class BaseStrategy(object):
    @property
    def is_default(self):
        return True

    def to_python(self, module="strategies"):
        raise NotImplementedError("subclasses must override this method")


class CustomTypes(BaseStrategy):
    __slots__ = ("strategy_name",)

    def __init__(self, strategy_name):
        self.strategy_name = strategy_name

    @classmethod
    def from_ros_type(cls, ros_type):
        return cls(ros_type_to_name(ros_type))

    def to_python(self, module="strategies"):
        return "draw({}())".format(self.strategy_name)


class JustValue(BaseStrategy):
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value

    @property
    def is_default(self):
        return False

    def to_python(self, module="strategies"):
        return "draw({}.just({}))".format(module, value_to_python(self.value))


class SampleValues(BaseStrategy):
    __slots__ = ("values",)

    def __init__(self, values):
        if not isinstance(values, tuple) or isinstance(values, list):
            raise TypeError("expected collection: " + repr(values))
        self.values = values

    @property
    def is_default(self):
        return False

    def to_python(self, module="strategies"):
        return "draw({}.sampled_from({}))".format(
            module, value_to_python(self.values))


class Numbers(BaseStrategy):
    __slots__ = ("ros_type", "min_value", "max_value")

    def __init__(self, ros_type):
        if not ros_type in ROS_NUMBER_TYPES:
            raise ValueError("unexpected ROS type: " + repr(ros_type))
        self.ros_type = ros_type
        self.min_value = None
        self.max_value = None

    @property
    def is_default(self):
        return self.min_value is None and self.max_value is None

    def to_python(self, module="strategies"):
        args = []
        if not self.min_value is None:
            args.append("min_value=" + value_to_python(self.min_value))
        if not self.max_value is None:
            args.append("max_value=" + value_to_python(self.max_value))
        args = ", ".join(args)
        return "draw({}({}))".format(ros_type_to_name(self.ros_type), args)


class Strings(BaseStrategy):
    def to_python(self, module="strategies"):
        return "draw(ros_string())"


class Booleans(BaseStrategy):
    def to_python(self, module="strategies"):
        return "draw(ros_bool())"


class Times(BaseStrategy):
    def to_python(self, module="strategies"):
        return "draw(ros_time())"


class Durations(BaseStrategy):
    def to_python(self, module="strategies"):
        return "draw(ros_duration())"


class Headers(BaseStrategy):
    def to_python(self, module="strategies"):
        return "draw(std_msgs_Header())"


class Arrays(BaseStrategy):
    __slots__ = ("elements", "length")

    def __init__(self, base_strategy, length=None):
        if not isinstance(base_strategy, BaseStrategy):
            raise TypeError("expected BaseStrategy, received "
                            + repr(base_strategy))
        self.elements = base_strategy
        self.length = length

    @property
    def is_default(self):
        return self.elements.is_default

    def to_python(self, module="strategies"):
        if self.length is None:
            tmp = "draw({}.lists(elements={}, min_size=0, max_size=256))"
            return tmp.format(module, self.base_strategy.to_python())
        assert self.length >= 0
        return "draw({}.tuples(*[{} for i in xrange({})]))".format(
            module, self.base_strategy.to_python(module=module), self.length)


class ByteArrays(BaseStrategy):
    __slots__ = ("length",)

    def __init__(self, length=None):
        BaseStrategy.__init__(self)
        self.length = length

    def to_python(self, module="strategies"):
        n = 256 if self.length is None else self.length
        assert n >= 0
        return "draw({}.binary(min_size=0, max_size={}))".format(module, n)


################################################################################
# Helper Functions
################################################################################

def ros_type_to_name(ros_type):
    if "/" in ros_type:
        return ros_type.replace("/", "_")
    elif ros_type == "Header":
        return "std_msgs_Header"
    else:
        return "ros_" + ros_type

def value_to_python(value):
    if isinstance(value, tuple) or isinstance(value, list):
        return "({})".format(", ".join(value_to_python(v) for v in value))
    if isinstance(value, Selector):
        return value.to_python()
    return repr(value)


################################################################################
# Test Code
################################################################################

if __name__ == "__main__":
    TEST_DATA = {
        "geometry_msgs/Twist": {
            "linear": TypeToken("geometry_msgs/Vector3"),
            "angular": TypeToken("geometry_msgs/Vector3")
        },
        "geometry_msgs/Vector3": {
            "x": TypeToken("float64"),
            "y": TypeToken("float64"),
            "z": TypeToken("float64")
        },
        "kobuki_msgs/BumperEvent": {
            "bumper": TypeToken("uint8"),
            "state": TypeToken("uint8")
        },
        "pkg/Msg": {
            "int": TypeToken("int32"),
            "float": TypeToken("float64"),
            "string": TypeToken("string"),
            "twist": TypeToken("geometry_msgs/Twist"),
            "int_list": ArrayTypeToken("int32"),
            "int_array": ArrayTypeToken("int32", length=3),
            "float_list": ArrayTypeToken("float64"),
            "float_array": ArrayTypeToken("float64", length=3),
            "string_list": ArrayTypeToken("string"),
            "string_array": ArrayTypeToken("string", length=3),
            "twist_list": ArrayTypeToken("geometry_msgs/Twist"),
            "twist_array": ArrayTypeToken("geometry_msgs/Twist", length=3),
            "nested_array": ArrayTypeToken("pkg/Nested", length=3)
        },
        "pkg/Nested": {
            "int": TypeToken("int32"),
            "int_array": ArrayTypeToken("int32", length=3),
            "nested_array": ArrayTypeToken("pkg/Nested2", length=3)
        },
        "pkg/Nested2": {
            "int": TypeToken("int32"),
            "int_array": ArrayTypeToken("int32", length=3)
        }
    }

    sm = StrategyMap(TEST_DATA)
    strategies = [s.to_python() for s in sm.defaults.itervalues()]
    print "\n\n".join(strategies)
    print ""

    nested = sm.make_custom("m", "pkg/Nested")
    assert "m" in sm.custom
    assert "pkg/Nested" in sm.custom["m"]
    assert "pkg/Nested2" in sm.custom["m"]
    nested2 = sm.get_custom("m", "pkg/Nested2")

    field_name = "int"
    field = FieldStrategy.make_default(field_name,
        TEST_DATA["pkg/Nested2"][field_name])
    field.modifiers.append(ExclusionModifier(0))
    nested2.fields[field_name] = field

    strat = StrategyReference.from_strategy(nested2)
    field_name = "nested_array"
    field = FieldStrategy.make_default(field_name,
        TEST_DATA["pkg/Nested"][field_name])
    field.modifiers.append(RandomIndexModifier(StrategyModifier(strat)))
    nested.fields[field_name] = field

    sm.complete_custom_strategies()
    print "\n\n".join(strategy.to_python()
                      for group in sm.custom.itervalues()
                      for strategy in group.itervalues())
