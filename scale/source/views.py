"""Defines the views for the RESTful source services"""
from __future__ import unicode_literals

import logging

from django.http.response import Http404
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response

import util.rest as rest_util
from ingest.models import Ingest
from ingest.serializers import IngestSerializer
from job.models import Job
from job.serializers import JobSerializer
from product.serializers import ProductFileSerializer
from source.models import SourceFile
from source.serializers import SourceFileSerializer, SourceFileUpdateSerializer, SourceFileDetailsSerializer
from source.serializers_extra import SourceFileDetailsSerializerV4
from storage.models import ScaleFile

logger = logging.getLogger(__name__)


class SourcesView(ListAPIView):
    """This view is the endpoint for retrieving source files."""
    queryset = ScaleFile.objects.all()
    serializer_class = SourceFileSerializer

    def list(self, request):
        """Retrieves the source files for a given time range and returns it in JSON form

        :param request: the HTTP GET request
        :type request: :class:`rest_framework.request.Request`
        :rtype: :class:`rest_framework.response.Response`
        :returns: the HTTP response to send back to the user
        """
        started = rest_util.parse_timestamp(request, 'started', required=False)
        ended = rest_util.parse_timestamp(request, 'ended', required=False)
        rest_util.check_time_range(started, ended)
        time_field = rest_util.parse_string(request, 'time_field', required=False,
                                            accepted_values=SourceFile.VALID_TIME_FIELDS)

        is_parsed = rest_util.parse_bool(request, 'is_parsed', required=False)
        file_name = rest_util.parse_string(request, 'file_name', required=False)

        order = rest_util.parse_string_list(request, 'order', required=False)

        sources = SourceFile.objects.get_sources(started, ended, time_field, is_parsed, file_name, order)

        page = self.paginate_queryset(sources)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)


class SourceDetailsView(RetrieveAPIView):
    """This view is the endpoint for retrieving/updating details of a source file."""
    queryset = ScaleFile.objects.all()

    # TODO: remove when REST API v4 is removed
    def get_serializer_class(self):
        """Override the serializer for legacy API calls."""
        if self.request.version == 'v4':
            return SourceFileDetailsSerializerV4
        return SourceFileDetailsSerializer

    def retrieve(self, request, source_id=None, file_name=None):
        """Retrieves the details for a source file and return them in JSON form

        :param request: the HTTP GET request
        :type request: :class:`rest_framework.request.Request`
        :param source_id: The id of the source
        :type source_id: int encoded as a string
        :param file_name: The name of the source
        :type file_name: string
        :rtype: :class:`rest_framework.response.Response`
        :returns: the HTTP response to send back to the user
        """

        if request.version == 'v4':
            return self.retrieve_v4(request, source_id, file_name)

        try:
            source = SourceFile.objects.get_details(source_id)
        except ScaleFile.DoesNotExist:
            raise Http404

        serializer = self.get_serializer(source)
        return Response(serializer.data)

    # TODO: remove when REST API v4 is removed
    def retrieve_v4(self, request, source_id=None, file_name=None):
        """Retrieves the details for a source file and return them in JSON form

        :param request: the HTTP GET request
        :type request: :class:`rest_framework.request.Request`
        :param source_id: The id of the source
        :type source_id: int encoded as a string
        :param file_name: The name of the source
        :type file_name: string
        :rtype: :class:`rest_framework.response.Response`
        :returns: the HTTP response to send back to the user
        """

        # Support retrieving by file name in addition to the usual identifier
        if file_name:
            sources = ScaleFile.objects.filter(file_name=file_name, file_type='SOURCE').values('id').order_by('-parsed')
            if not sources:
                raise Http404
            source_id = sources[0]['id']

        include_superseded = rest_util.parse_bool(request, 'include_superseded', required=False)

        try:
            source = SourceFile.objects.get_details_v4(source_id, include_superseded=include_superseded)
        except ScaleFile.DoesNotExist:
            raise Http404

        serializer = self.get_serializer(source)
        return Response(serializer.data)


class SourceIngestsView(ListAPIView):
    """This view is the endpoint for retrieving a list of all ingests related to a source file."""
    queryset = Ingest.objects.all()
    serializer_class = IngestSerializer

    def list(self, request, source_id=None):
        """Retrieves the ingests for a given source file ID and returns them in JSON form

        :param request: the HTTP GET request
        :type request: :class:`rest_framework.request.Request`
        :param source_id: The id of the source
        :type source_id: int encoded as a string
        :rtype: :class:`rest_framework.response.Response`
        :returns: the HTTP response to send back to the user
        """

        try:
            ScaleFile.objects.get(id=source_id, file_type='SOURCE')
        except ScaleFile.DoesNotExist:
            raise Http404

        started = rest_util.parse_timestamp(request, 'started', required=False)
        ended = rest_util.parse_timestamp(request, 'ended', required=False)
        rest_util.check_time_range(started, ended)

        ingest_statuses = rest_util.parse_string_list(request, 'status', required=False)
        strike_ids = rest_util.parse_int_list(request, 'strike_id', required=False)
        scan_ids = rest_util.parse_int_list(request, 'scan_id', required=False)
        order = rest_util.parse_string_list(request, 'order', required=False)

        ingests = SourceFile.objects.get_source_ingests(source_id, started=started, ended=ended,
                                                        statuses=ingest_statuses, scan_ids=scan_ids,
                                                        strike_ids=strike_ids, order=order)

        page = self.paginate_queryset(ingests)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)


class SourceJobsView(ListAPIView):
    """This view is the endpoint for retrieving a list of all jobs related to a source file."""
    queryset = Job.objects.all()
    serializer_class = JobSerializer

    def list(self, request, source_id=None):
        """Retrieves the jobs for a given source file ID and returns them in JSON form

        :param request: the HTTP GET request
        :type request: :class:`rest_framework.request.Request`
        :param source_id: The id of the source
        :type source_id: int encoded as a string
        :rtype: :class:`rest_framework.response.Response`
        :returns: the HTTP response to send back to the user
        """

        try:
            ScaleFile.objects.get(id=source_id, file_type='SOURCE')
        except ScaleFile.DoesNotExist:
            raise Http404

        started = rest_util.parse_timestamp(request, 'started', required=False)
        ended = rest_util.parse_timestamp(request, 'ended', required=False)
        rest_util.check_time_range(started, ended)

        statuses = rest_util.parse_string_list(request, 'status', required=False)
        job_ids = rest_util.parse_int_list(request, 'job_id', required=False)
        job_type_ids = rest_util.parse_int_list(request, 'job_type_id', required=False)
        job_type_names = rest_util.parse_string_list(request, 'job_type_name', required=False)
        job_type_categories = rest_util.parse_string_list(request, 'job_type_category', required=False)
        error_categories = rest_util.parse_string_list(request, 'error_category', required=False)
        include_superseded = rest_util.parse_bool(request, 'include_superseded', required=False)

        order = rest_util.parse_string_list(request, 'order', required=False)

        jobs = SourceFile.objects.get_source_jobs(source_id, started=started, ended=ended, statuses=statuses,
                                                  job_ids=job_ids, job_type_ids=job_type_ids,
                                                  job_type_names=job_type_names,
                                                  job_type_categories=job_type_categories,
                                                  error_categories=error_categories,
                                                  include_superseded=include_superseded, order=order)

        page = self.paginate_queryset(jobs)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)


class SourceProductsView(ListAPIView):
    """This view is the endpoint for retrieving products produced from a source file"""
    queryset = ScaleFile.objects.all()
    serializer_class = ProductFileSerializer

    def list(self, request, source_id=None):
        """Retrieves the products for a given source file ID and returns them in JSON form

        :param request: the HTTP GET request
        :type request: :class:`rest_framework.request.Request`
        :param source_id: The id of the source
        :type source_id: int encoded as a string
        :rtype: :class:`rest_framework.response.Response`
        :returns: the HTTP response to send back to the user
        """

        try:
            ScaleFile.objects.get(id=source_id, file_type='SOURCE')
        except ScaleFile.DoesNotExist:
            raise Http404

        started = rest_util.parse_timestamp(request, 'started', required=False)
        ended = rest_util.parse_timestamp(request, 'ended', required=False)
        rest_util.check_time_range(started, ended)

        batch_ids = rest_util.parse_int_list(request, 'batch_id', required=False)
        job_type_ids = rest_util.parse_int_list(request, 'job_type_id', required=False)
        job_type_names = rest_util.parse_string_list(request, 'job_type_name', required=False)
        job_type_categories = rest_util.parse_string_list(request, 'job_type_category', required=False)
        is_operational = rest_util.parse_bool(request, 'is_operational', required=False)
        is_published = rest_util.parse_bool(request, 'is_published', required=False)
        file_name = rest_util.parse_string(request, 'file_name', required=False)
        job_output = rest_util.parse_string(request, 'job_output', required=False)
        recipe_ids = rest_util.parse_int_list(request, 'recipe_id', required=False)
        recipe_type = rest_util.parse_string(request, 'recipe_type', required=False)
        recipe_job = rest_util.parse_string(request, 'recipe_job', required=False)

        source_started = rest_util.parse_timestamp(request, 'source_started', required=False)
        source_ended = rest_util.parse_timestamp(request, 'source_ended', required=False)
        rest_util.check_time_range(source_started, source_ended)

        order = rest_util.parse_string_list(request, 'order', required=False)

        products = SourceFile.objects.get_source_products(source_id, started=started, ended=ended, batch_ids=batch_ids,
                                                          job_type_ids=job_type_ids, job_type_names=job_type_names,
                                                          job_type_categories=job_type_categories,
                                                          is_operational=is_operational, is_published=is_published,
                                                          file_name=file_name, job_output=job_output,
                                                          recipe_ids=recipe_ids, recipe_type=recipe_type,
                                                          recipe_job=recipe_job, source_started=source_started,
                                                          source_ended=source_ended, order=order)

        page = self.paginate_queryset(products)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)


class SourceUpdatesView(ListAPIView):
    """This view is the endpoint for retrieving source file updates over a given time range."""
    queryset = ScaleFile.objects.all()
    serializer_class = SourceFileUpdateSerializer

    def list(self, request):
        """Retrieves the source file updates for a given time range and returns it in JSON form

        :param request: the HTTP GET request
        :type request: :class:`rest_framework.request.Request`
        :rtype: :class:`rest_framework.response.Response`
        :returns: the HTTP response to send back to the user
        """
        started = rest_util.parse_timestamp(request, 'started', required=False)
        ended = rest_util.parse_timestamp(request, 'ended', required=False)
        rest_util.check_time_range(started, ended)
        time_field = rest_util.parse_string(request, 'time_field', required=False,
                                            accepted_values=SourceFile.VALID_TIME_FIELDS)

        is_parsed = rest_util.parse_bool(request, 'is_parsed', required=False)
        file_name = rest_util.parse_string(request, 'file_name', required=False)

        order = rest_util.parse_string_list(request, 'order', required=False)

        sources = SourceFile.objects.get_sources(started, ended, time_field, is_parsed, file_name, order)

        page = self.paginate_queryset(sources)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)
