#!/usr/bin/env python3
"""Fail-closed GHCR privacy/immutability probe; never prints credential material."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

PACKAGE = 'https://api.github.com/users/tgenman/packages/container/oko'


def request(url, headers):
    try:
        response = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    except OSError:
        raise RuntimeError('Registry state could not be verified') from None
    with response:
        body = response.read(2_000_001)
        if len(body) > 2_000_000:raise RuntimeError('Registry response exceeded limit')
        try:data=json.loads(body)
        except ValueError:raise RuntimeError('Registry response was not JSON') from None
        return response.status, data, dict(response.headers)


def probe(sha, token, actor):
    if not re.fullmatch('[0-9a-f]{40}', sha):raise ValueError('Invalid release SHA')
    status, package, _ = request(PACKAGE, {'Authorization':'Bearer '+token, 'Accept':'application/vnd.github+json'})
    if status == 404:
        return dict(package='absent', present=False, digest=None)
    if status != 200:raise RuntimeError('Package visibility could not be verified: HTTP '+str(status))
    if package.get('visibility') != 'private':raise RuntimeError('Refusing to publish or reuse a non-private package')
    basic = base64.b64encode((actor+':'+token).encode()).decode()
    url = 'https://ghcr.io/token?'+urllib.parse.urlencode(dict(service='ghcr.io', scope='repository:tgenman/oko:pull'))
    status, authorization, _ = request(url, {'Authorization':'Basic '+basic})
    if status != 200 or not isinstance(authorization.get('token'), str):
        raise RuntimeError('Registry read authorization failed')
    status, manifest, headers = request('https://ghcr.io/v2/tgenman/oko/manifests/'+sha,
        {'Authorization':'Bearer '+authorization['token'],
         'Accept':'application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json'})
    if status == 404 and any(item.get('code') == 'MANIFEST_UNKNOWN' for item in manifest.get('errors', [])):
        return dict(package='private', present=False, digest=None)
    if status != 200:raise RuntimeError('Immutable tag could not be verified: HTTP '+str(status))
    digest = next((value for name,value in headers.items() if name.lower()=='docker-content-digest'), None)
    if not isinstance(digest,str) or not re.fullmatch('sha256:[0-9a-f]{64}',digest):
        raise RuntimeError('Registry did not return an immutable digest')
    return dict(package='private', present=True, digest=digest)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--sha',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--require-present',action='store_true')
    args=parser.parse_args()
    value=probe(args.sha,os.environ['PACKAGE_TOKEN'],os.environ['GITHUB_ACTOR'])
    if args.require_present and not value['present']:raise SystemExit('Published release was not found')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(value)+'\n')
    if path:=os.environ.get('GITHUB_OUTPUT'):
        with open(path,'a') as output:
            output.write('present='+str(value['present']).lower()+'\n')
            output.write('digest='+(value['digest'] or '')+'\n')


if __name__=='__main__':
    main()
