#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Digest-only readback of the seven controller resources excluded from restore."""
import hashlib
import json
from pathlib import Path
import sys

CONFIGMAPS=('istio-ca-crl','istio-ca-root-cert','kube-root-ca.crt')
VIRTUAL_SERVICE='cloudtak-tenant-cloudtak-map'

def require(value):
    if not value:raise ValueError('controller resource invariant')

def unique(pairs):
    result={}
    for key,value in pairs:
        require(key not in result);result[key]=value
    return result

def load(path,limit=512*1024):
    with Path(path).open('rb') as stream:raw=stream.read(limit+1)
    require(len(raw)<=limit)
    return json.loads(raw,object_pairs_hook=unique)

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def live(obj,kind,namespace,name):
    require(obj.get('kind')==kind)
    meta=obj.get('metadata',{})
    require(meta.get('namespace')==namespace and meta.get('name')==name and
            isinstance(meta.get('uid'),str) and bool(meta['uid']) and not meta.get('deletionTimestamp'))
    return meta

def snapshot(folder):
    folder=Path(folder);result={}
    for namespace in ('takserver','cloudtak'):
        inventory=load(folder/(namespace+'-configmaps.json'))
        require(isinstance(inventory,dict) and isinstance(inventory.get('items'),list) and len(inventory['items'])==3)
        seen=set()
        for obj in inventory['items']:
            name=obj.get('metadata',{}).get('name');require(name in CONFIGMAPS and name not in seen);seen.add(name)
            live(obj,'ConfigMap',namespace,name)
            data=obj.get('data',{});binary=obj.get('binaryData',{})
            require(isinstance(data,dict) and isinstance(binary,dict) and bool(data or binary))
            require(all(isinstance(k,str) and isinstance(v,str) for values in (data,binary) for k,v in values.items()))
            result[namespace+'/ConfigMap/'+name]=digest({'data':data,'binaryData':binary})
    package=load(folder/'cloudtak-package.json')
    pkg=live(package,'Package','cloudtak','cloudtak');require(package.get('apiVersion')=='uds.dev/v1alpha1')
    require(type(pkg.get('generation')) is int and pkg['generation']>0)
    vs=load(folder/'cloudtak-virtualservice.json');meta=live(vs,'VirtualService','cloudtak',VIRTUAL_SERVICE)
    require(vs.get('apiVersion') in ('networking.istio.io/v1','networking.istio.io/v1beta1'))
    owners=meta.get('ownerReferences',[])
    require(len(owners)==1 and all(owners[0].get(key)==value for key,value in
            {'apiVersion':'uds.dev/v1alpha1','kind':'Package','name':'cloudtak','uid':pkg['uid']}.items()))
    labels=meta.get('labels',{})
    require(labels.get('uds/package')=='cloudtak' and labels.get('uds/generation')==str(pkg.get('generation')))
    require(isinstance(vs.get('spec'),dict) and bool(vs['spec']))
    result['cloudtak/VirtualService/'+VIRTUAL_SERVICE]=digest(vs['spec'])
    return {'schema':1,'resources':result}

def main():
    try:
        require(len(sys.argv) in (3,4))
        mode,folder=sys.argv[1:3];require(mode in ('snapshot','verify'))
        current=snapshot(folder)
        if mode=='verify':
            require(len(sys.argv)==4 and load(sys.argv[3],16384)==current)
        else:
            require(len(sys.argv)==3);print(json.dumps(current,sort_keys=True))
        return 0
    except (ValueError,TypeError,KeyError,OSError,AttributeError):
        print('controller resource readback failed',file=sys.stderr);return 1

if __name__=='__main__':sys.exit(main())
