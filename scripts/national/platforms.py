"""Attach reviewed boarding labels to exact CIS calls without inventing paths."""
import datetime as dt
import re
from urllib.parse import urlsplit
from .common import ImportErrorDetail, digest, finalize


def stop_fingerprint(stop):
    return digest(stop)


def apply_platform_mappings(network, catalog):
    if not isinstance(catalog, dict) or catalog.get('schema') != 1 or not isinstance(catalog.get('reviews'), list) or not isinstance(catalog.get('calls'), list):
        raise ImportErrorDetail('Invalid platform mapping schema')
    reviews = {}
    previous_reviews = network.get('platformMappingEvidence', {}).get('reviews', [])
    if not isinstance(previous_reviews, list):
        raise ImportErrorDetail('Invalid previous platform reviews')
    for review in [*previous_reviews, *catalog['reviews']]:
        if not isinstance(review, dict):
            raise ImportErrorDetail('Invalid platform review')
        key = review.get('id')
        if not isinstance(key, str) or not key or key in reviews:
            raise ImportErrorDetail('Platform review needs a unique identity')
        if not isinstance(review.get('evidence'), str):
            raise ImportErrorDetail('Platform mapping requires verified source evidence')
        source = urlsplit(review['evidence'])
        if source.scheme != 'https' or not source.hostname or source.username or source.password or review.get('verified') is not True:
            raise ImportErrorDetail('Platform mapping requires verified source evidence')
        try:
            stamp = dt.datetime.fromisoformat(review['verifiedAt'].replace('Z', '+00:00'))
            valid_from = dt.date.fromisoformat(review['validFrom'])
            if stamp.tzinfo is None or valid_from > dt.date.fromisoformat(network['start']):
                raise ValueError('Review does not cover the network start')
            if review.get('validUntil') and dt.date.fromisoformat(review['validUntil']) < dt.date.fromisoformat(network['end']):
                raise ValueError('Review does not cover the network end')
            if review.get('reviewedFrom') and dt.date.fromisoformat(review['reviewedFrom']) > dt.date.fromisoformat(network['start']):
                raise ValueError('Review does not cover the source horizon start')
            if review.get('reviewedUntil') and dt.date.fromisoformat(review['reviewedUntil']) < dt.date.fromisoformat(network['end']):
                raise ValueError('Review expires before the source horizon end')
        except (KeyError, ValueError, TypeError, AttributeError) as error:
            raise ImportErrorDetail('Platform review has invalid dates or a different source horizon') from error
        reviews[key] = review
    indexes = {stop['id']: i for i, stop in enumerate(network['stops'])}
    changed = {}
    platform_labels = {}
    for stop in network['stops']:
        physical, label = stop.get('physicalPlatformId'), stop.get('platform')
        if stop.get('physicalPlatformVerified') is True and physical and label:
            if physical in platform_labels and platform_labels[physical] != label:
                raise ImportErrorDetail('Existing physical platform has conflicting labels')
            platform_labels[physical] = label
    for mapping in catalog['calls']:
        if not isinstance(mapping, dict):
            raise ImportErrorDetail('Invalid platform call mapping')
        key = mapping.get('stopId')
        if key not in indexes or key in changed:
            raise ImportErrorDetail('Platform call is missing or mapped more than once')
        stop = network['stops'][indexes[key]]
        if stop.get('source') != 'cis-ostrava' or not isinstance(stop.get('sourceCall'), dict) or not isinstance(stop['sourceCall'].get('patternSha256'), str) or not re.fullmatch('[a-f0-9]{64}', stop['sourceCall']['patternSha256']):
            raise ImportErrorDetail('Only an exact source pattern call can receive this platform mapping')
        fingerprint = mapping.get('stopSha256', '')
        if not isinstance(fingerprint, str) or not re.fullmatch('[a-f0-9]{64}', fingerprint) or fingerprint != stop_fingerprint(stop):
            raise ImportErrorDetail('Platform source call changed; re-verification required')
        review = reviews.get(mapping.get('reviewId'))
        platform, label = mapping.get('physicalPlatformId'), mapping.get('platform')
        if review is None or not isinstance(platform, str) or not platform or not isinstance(label, str) or not label.strip():
            raise ImportErrorDetail('Platform identity, label and review are required')
        if platform in platform_labels and platform_labels[platform] != label:
            raise ImportErrorDetail('One physical platform has conflicting labels')
        platform_labels[platform] = label
        changed[key] = {**stop, 'physicalPlatformId': platform, 'physicalPlatformVerified': True,
                        'platform': label, 'platformEvidence': {'reviewId': review['id'], 'url': review['evidence'],
                        'verifiedAt': review['verifiedAt'], 'validFrom': review['validFrom']}}
        if review.get('validUntil'):
            changed[key]['platformEvidence']['validUntil'] = review['validUntil']
        for field in ['reviewedFrom', 'reviewedUntil']:
            if field in review:
                changed[key]['platformEvidence'][field] = review[field]
    # Copy only modified metadata. Trips, clocks, groups, coordinates and paths
    # remain exactly as imported; a known boarding label is not a walking edge.
    output = dict(network)
    output['stops'] = [changed.get(stop['id'], stop) for stop in network['stops']]
    output['active'] = dict(network['active'])
    output['coverage'] = dict(network.get('coverage', {}))
    output['coverage']['physicalPlatformMappingsVerified'] = sum(stop.get('physicalPlatformVerified') is True for stop in output['stops'])
    labelled_calls = sum(stop.get('physicalPlatformVerified') is True
        and stop.get('platformEvidence', {}).get('reviewId') in reviews for stop in output['stops'])
    output['platformMappingEvidence'] = {'reviews': list(reviews.values()), 'callCount': labelled_calls, 'walkingPathsAdded': 0}
    return finalize(output)
