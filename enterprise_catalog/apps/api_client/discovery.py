"""
Discovery service api client code.
"""
import logging
import time

import requests
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings

from .base_oauth import BaseOAuthClient
from .constants import (
    DISCOVERY_COURSES_ENDPOINT,
    DISCOVERY_OFFSET_SIZE,
    DISCOVERY_PROGRAMS_ENDPOINT,
    DISCOVERY_SEARCH_ALL_ENDPOINT,
)


LOGGER = logging.getLogger(__name__)


class DiscoveryApiClient(BaseOAuthClient):
    """
    Object builds an API client to make calls to the Discovery Service.
    """

    # the maximum number of retries to attempt a call
    MAX_RETRIES = getattr(settings, "ENTERPRISE_DISCOVERY_CLIENT_MAX_RETRIES", 4)
    # the number of seconds to sleep beteween tries, which is doubled every attempt
    BACKOFF_FACTOR = getattr(settings, "ENTERPRISE_DISCOVERY_CLIENT_BACKOFF_FACTOR", 2)
    # the number of seconds to wait for a response
    HTTP_TIMEOUT = getattr(settings, "ENTERPRISE_DISCOVERY_CLIENT_TIMEOUT", 15)

    def _calculate_backoff(self, attempt_count):
        """
        Calculate the seconds to sleep based on attempt_count
        """
        return (self.BACKOFF_FACTOR * (2 ** (attempt_count - 1)))

    def _retrieve_metadata_for_content_filter(self, content_filter, page, request_params):
        """
        Makes a request to discovery's /search/all/ endpoint with the specified
        content_filter, page, and request_params
        """
        LOGGER.info(f'Retrieving results from course-discovery for page {page}...')
        attempts = 0
        while True:
            attempts = attempts + 1
            successful = True
            exception = None
            try:
                response = self.client.post(
                    DISCOVERY_SEARCH_ALL_ENDPOINT,
                    json=content_filter,
                    params=request_params,
                    timeout=self.HTTP_TIMEOUT,
                )
                successful = response.status_code < 400
                elapsed_seconds = response.elapsed.total_seconds()
                LOGGER.info(
                    f'Retrieved results from course-discovery for page {page} in '
                    f'retrieve_metadata_for_content_filter_seconds={elapsed_seconds} seconds.'
                )
            except requests.exceptions.RequestException as err:
                exception = err
                LOGGER.exception(f'Error while retrieving results from course-discovery for page {page}')
                successful = False
            if attempts <= self.MAX_RETRIES and not successful:
                sleep_seconds = self._calculate_backoff(attempts)
                LOGGER.warning(
                    f'failed request detected from {DISCOVERY_SEARCH_ALL_ENDPOINT}, '
                    'backing-off before retrying, '
                    f'sleeping {sleep_seconds} seconds...'
                )
                time.sleep(sleep_seconds)
            else:
                if exception:
                    raise exception
                break
        return response.json()

    def get_metadata_by_query(self, catalog_query):
        """
        Return results from the discovery service's search/all endpoint.

        Arguments:
            catalog_query (CatalogQuery): Catalog Query object to retrieve metadata for

        Returns:
            list: a list of the results, or None if there was an error calling the discovery service.
        """
        request_params = {
            # Omit non-active course runs from the course-discovery results
            'exclude_expired_course_run': True,
            # Increase number of results per page for the course-discovery response
            'page_size': 100,
            # Ensure paginated results are consistently ordered by `aggregation_key` and `start`
            'ordering': 'aggregation_key,start',
            # Ensure to fetch learner pathways as part of search/all endpoint response.
            'include_learner_pathways': True,
        }

        page = 1
        results = []
        try:
            content_filter = catalog_query.content_filter
            response = self._retrieve_metadata_for_content_filter(content_filter, page, request_params)
            results += response.get('results', [])
            # Traverse all pages and concatenate results
            while response.get('next'):
                page += 1
                request_params.update({'page': page})
                response = self._retrieve_metadata_for_content_filter(content_filter, page, request_params)
                results += response.get('results', [])
        except Exception as exc:
            LOGGER.exception(
                'Could not retrieve content items from course-discovery (page %s) for catalog query %s: %s',
                page,
                catalog_query,
                exc,
            )
            raise exc

        return results

    def _retrieve_courses(self, offset, request_params):
        """
        Makes a request to discovery's /api/v1/courses/ endpoint with the specified offset and request_params
        """
        LOGGER.info('Retrieving courses from course-discovery for offset %s...', offset)
        response = self.client.get(
            DISCOVERY_COURSES_ENDPOINT,
            params=request_params,
            timeout=self.HTTP_TIMEOUT,
        ).json()
        return response

    def get_courses(self, query_params=None):
        """
        Return results from the discovery service's /courses endpoint.

        Arguments:
            query_params (dict): additional query params for the rest api endpoint
                we're hitting. e.g. - {'limit': 100}

        Returns:
            list: a list of the results, or None if there was an error calling the discovery service.
        """
        request_params = {
            'ordering': 'key',
            'limit': DISCOVERY_OFFSET_SIZE,
        }
        request_params.update(query_params or {})

        courses = []
        offset = 0
        try:
            response = self._retrieve_courses(offset, request_params)
            courses += response.get('results')
            # Traverse all pages and concatenate results
            while response.get('next'):
                offset += DISCOVERY_OFFSET_SIZE
                request_params.update({'offset': offset})
                response = self._retrieve_courses(offset, request_params)
                courses += response.get('results', [])
        except SoftTimeLimitExceeded as exc:
            LOGGER.warning(
                'A task reached the soft time limit while traversing courses. %d courses already retrieved'
                ' from course-discovery will continue to be processed: %s',
                len(courses),
                exc,
            )
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.error(
                'Could not get courses from course-discovery (offset %d) with query params %s: %s',
                offset,
                request_params,
                exc,
            )

        return courses

    def _retrieve_programs(self, offset, request_params):
        """
        Makes a request to discovery's /api/v1/programs/ endpoint with the specified offset and request_params
        """
        LOGGER.info('Retrieving programs from course-discovery for offset %s...', offset)
        response = self.client.get(
            DISCOVERY_PROGRAMS_ENDPOINT,
            params=request_params,
            timeout=self.HTTP_TIMEOUT,
        ).json()
        return response

    def get_programs(self, query_params=None):
        """
        Return results from the discovery service's /programs endpoint.

        Arguments:
            query_params (dict): additional query params for the rest api endpoint
                we're hitting. e.g. - {'limit': 100}

        Returns:
            list: a list of the results, or None if there was an error calling the discovery service.
        """
        request_params = {
            'ordering': 'key',
            'limit': DISCOVERY_OFFSET_SIZE,
            'extended': 'True',
        }
        request_params.update(query_params or {})

        programs = []
        offset = 0
        try:
            response = self._retrieve_programs(offset, request_params)
            programs += response.get('results')
            # Traverse all pages and concatenate results
            while response.get('next'):
                offset += DISCOVERY_OFFSET_SIZE
                request_params.update({'offset': offset})
                response = self._retrieve_programs(offset, request_params)
                programs += response.get('results', [])
        except SoftTimeLimitExceeded as exc:
            LOGGER.warning(
                'A task reached the soft time limit while traversing programs. %d programs already retrieved'
                ' from course-discovery will continue to be processed: %s',
                len(programs),
                exc,
            )
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.error(
                'Could not get programs from course-discovery (offset %d) with query params %s: %s',
                offset,
                request_params,
                exc,
            )

        return programs


class CatalogQueryMetadata:
    """
    Metadata for a given CatalogQuery from the Discovery API.

    """
    def __init__(self, catalog_query):
        """
        Initialize a Catalog Query details instance and load data from
        the Discovery API client.

        Arguments:
            catalog_query (CatalogQuery): Catalog Query to retrieve metadata for
        """
        self.catalog_query = catalog_query
        self.catalog_query_data = self._get_catalog_query_metadata(catalog_query)

    @property
    def metadata(self):
        """
        Return catalog query metadata (will be an empty dict if unavailable)
        """
        return self.catalog_query_data

    def _get_catalog_query_metadata(self, catalog_query):
        """
        Retrieve JSON data containing Catalog Query metadata for the given catalog_query_id
        by making a call to Discovery API Client.

        Arguments:
            catalog_query (CatalogQuery): Catalog Query object

        Returns:
            customer_data (dict): Enterprise Customer details OR
                Empty dictionary if no data found from API.
        """
        client = DiscoveryApiClient()
        catalog_query_data = client.get_metadata_by_query(catalog_query)
        return catalog_query_data
