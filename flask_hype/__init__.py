from flask import request
from flask.views import View
import werkzeug
from werkzeug.routing import BaseConverter

from hype import Registry, Handler, Context
from hype.registry import Namespace


def resource_view(targets):
    """Creates a resource view for a specific Resource-class.

    :param targets: A list of RoutingTargets that are tried for matches
    """
    class ResourceView(View):
        def dispatch_request(self, **kwargs):
            # kwargs are the parameters extracted from the URL

            # create a context
            print 'DISPATCHING REQUEST TO', targets
            return Handler.dispatch(
                Context, targets,
                request=request,
                uri=request.path,
                method=request.method,
                params=kwargs)

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
        return ''.join(parts)


    def _format_target(self, path):
        # unclear if this function is necessary
        return path

    def connect(self, app_or_bp):
        """Connects a namespace onto a Flask application or blueprint,
        setting up routes.
        :param app_or_bp: The app or blueprint to connect the namespace to.
        """

        # converters
        # for resource in self.resources():
        #     converter_cls = resource.converter_cls
        #     if not converter_cls:
        #         continue
        #     app_or_bp.url_map.converters[converter_cls.name] = converter_cls

        converters = app_or_bp.url_map.converters
        for resource in self.resources():
            converters[resource._type_] = (converter(resource))

        # register views
        submount_rules = {}

        for name, submounts, path, methods, targets in self.routes():
            # there used to be a sanity check for duplicate views here,
            # however that does not work with blueprints

            # functools.partial(
            #     handler_cls.dispatch,
            #     context_cls,
            #     targets=targets,
            #     )

            view_func = resource_view(targets).as_view(name)
            app_or_bp.view_functions[name] = view_func
            rule = werkzeug.routing.Rule(path, endpoint=name, methods=methods)

            for submount in submounts:
                submount_rules.setdefault(submount, []).append(rule)

        # map view rules
        submounts = []
        for submount, rules in submount_rules.items():
            if submount is None:
                submounts.append(werkzeug.routing.Submount('', rules))
            else:
                submounts.append(werkzeug.routing.Submount(submount, rules))
        for submount in submounts:
            app_or_bp.url_map.add(submount)


class FlaskRegistry(Registry):
    """A Flask-compatible Namespace implementation.

    TODO: Check if this is really flask specific."""
    def connect(self, *args, **kwargs):
        """Connects the root namespace using the specified arguments."""
        return self.root.connect(*args, **kwargs)

    namespace_cls = FlaskNamespace
