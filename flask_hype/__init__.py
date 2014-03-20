from flask import request, Blueprint
from flask_arrest.helpers import serialize_response
from flask.views import View
import inflection
import werkzeug
from werkzeug.routing import BaseConverter
from hype import Registry, Handler, Context, Resource
from hype.registry import Namespace
from hype.resource import ResourceMeta

from flask.ext.hype.util import recordable


def resource_view(targets):
    """Creates a resource view for a specific Resource-class.

    :param targets: A list of RoutingTargets that are tried for matches
    """
    class ResourceView(View):
        def dispatch_request(self, **kwargs):
            # kwargs are the parameters extracted from the URL

            # call the handler and save the return value
            result = Handler.dispatch(
                Context, targets,
                request=request,
                uri=request.path,
                method=request.method,
                **kwargs)

            # serialize the response using flask-arrest
            return serialize_response(result)

    return ResourceView


class ResourceNotFoundError(LookupError):
    pass


def converter(obj_type):
    """Given a Resource-class, create a werkzeug-compatible converter that
    will lookup an id and return an instance of the resource."""

    class ResourceConverter(BaseConverter):
        def to_python(self, resource_id):
            try:
                # FIXME: hype should be altered instead, returning a custom
                # class for resource lookup errors that dervies from
                # LookupError
                return obj_type.from_id(resource_id)
            except LookupError as e:
                raise ResourceNotFoundError(*e.args)

        def to_url(self, obj):
            return obj.to_id()

    return ResourceConverter


class FlaskNamespace(Namespace):
    def get_endpoint_name(self, app_or_bp, name):
        """Translate an endpoint name from hype to Flask-Hype.

        Endpoint in hype usually use a dot (.) as separators, which is very
        problematic in a Flask context, as dots are reserved to indicate
        blueprints in view function names.

        Namespaces endpoints therefore are renamed using this function; any
        dot introduced on behalf of hype is replaced with a colon. If the
        endpoint is to be registered on a :class:`~flask.Blueprint`,
        the blueprint's name is prefixed with a dot as well.

        :param app_or_bp: A :class:`~flask.Flask` or
                          :class:`~flask.Blueprint` object.
        :param name: The name to translate.
        :return: The new name.
        """

        name = name.replace('.', ':')
        if isinstance(app_or_bp, Blueprint):
            name = '{}.{}'.format(app_or_bp.name, name)
        return name

    def _format_path(self, path):
        # given a list of path components, formats those into a
        # an actual path
        if not isinstance(path, (list, tuple)):
            path = [path]

        parts = []
        for part in path:
            if part.startswith('/'):
                # literal
                parts.append(part)
                continue

            # skip empty parts
            if not part:
                continue

            resource_cls = self.matching_resource(part)
            if not resource_cls:
                raise ValueError(
                    'Path "{}" has unknown resource "{}"'.format(path, part)
                )

            if resource_cls._collection_ == part:
                # collection simply use the plural name
                part = '/{}'.format(resource_cls._plural_name_)
            else:
                # single resources need to be addressed with the id
                part = '/{}/<{}:{}>'.format(
                    resource_cls._plural_name_,
                    resource_cls._type_,
                    resource_cls._name_,
                )
            parts.append(part)

        # append trailing slash
        parts.append('/')
        return ''.join(parts)

    def connect(self, app_or_bp):
        """Connects a namespace onto a Flask application or blueprint. This
        will register the necessary URL converters onto the app, as well as
        setting up routes to the resources.

        If a blueprint is passed, all actions that cannot be performed on
        the blueprint will be recorded to be applied to the application upon
        Blueprint registration.

        :param app_or_bp: The app or blueprint to connect the namespace to.
        """

        @recordable()
        def add_resource_converter(app, resource):
            app.url_map.converters[resource._type_] = converter(resource)

        for resource in self.resources():
            add_resource_converter(app_or_bp, resource)

        # register views
        submount_rules = {}

        for name, submounts, path, methods, targets in self.routes():
            # there used to be a sanity check for duplicate views here,
            # however that does not work with blueprints
            name = self.get_endpoint_name(app_or_bp, name)

            view_func = resource_view(targets).as_view(name)
            app_or_bp.endpoint(name)(view_func)
            rule = werkzeug.routing.Rule(path, endpoint=name, methods=methods)

            for submount in submounts:
                submount_rules.setdefault(submount, []).append(rule)

        # compile a list of url_rules that need to be added to the url map
        url_rules = []
        for prefix, rules in submount_rules.items():
            if submount is None:
                 url_rules.extend(rules)
            else:
                 url_rules.append(werkzeug.routing.Submount(submount, rules))

        def add_rules_to_blueprint(bp, rules):
            """Helper function for adding rules to a blueprint,
            honoring the url_prefix.

            :param bp: Blueprint to add rules to.
            :param rules: A sequence of werkzeug.routing.Rule instances or
                          similar."""

            # note: do *not* confuse this or any of the stuff below with
            #       add_url_rule - it's a completely different function!
            def add_rules(state):
                _rules = rules

                # if the blueprint has been mounted with a prefix, we need
                # to honor this prefix on our submount
                if state.url_prefix:
                    _rules = werkzeug.routing.Submount(
                        state.url_prefix, rules).get_rules(state.app.url_map)

                for rule in _rules:
                    state.app.url_map.add(rule)

            bp.record(add_rules)

        # add all the generated rules to the url map
        url_map = getattr(app_or_bp, 'url_map', None)
        if url_map:
            # regular app, just slap the the rules onto the url map
            map(url_map.add, url_rules)
        else:
            # it's a blueprint, we can only record the addition of the rules
            add_rules_to_blueprint(app_or_bp, url_rules)


class FlaskRegistry(Registry):
    """A Flask-compatible Namespace implementation.

    TODO: Check if this is really flask specific."""
    def connect(self, *args, **kwargs):
        """Connects the root namespace using the specified arguments."""
        return self.root.connect(*args, **kwargs)

    namespace_cls = FlaskNamespace


class FlaskHypeResourceMeta(ResourceMeta):
    def __new__(mcs, name, bases, dikt):

        # we need to skip FlaskHypeResource, otherwise all resources
        # will get a default _type_ and _collection_ named 'FlaskHypeResource'
        # FIXME: maybe alter hype to make this not depend on hacks like below
        if name != 'FlaskHypeResource':
            # auto-generate singular and plural forms
            if '_type_' not in dikt:
                dikt['_type_'] = inflection.underscore(name)

            if '_collection_' not in dikt:
                dikt['_collection_'] = inflection.pluralize(dikt['_type_'])

        return super(FlaskHypeResourceMeta, mcs).__new__(
            mcs, name, bases, dikt
        )



class FlaskHype(object):
    def __init__(self, app=None):
        if app:
            self.init_app(app)
        self.registry = FlaskRegistry()
        self.Resource = self.make_resource_base(self.registry)

    def make_resource_base(self, hype_registry):
        class FlaskHypeResource(Resource):
            __metaclass__ = FlaskHypeResourceMeta

            registry = hype_registry

        return FlaskHypeResource

    def init_app(self, app):
        self.registry.connect(app)

    init_blueprint = init_app
