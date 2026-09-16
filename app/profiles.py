"""Portable, data-only compression profiles; no file paths or FFmpeg commands."""
import json
from dataclasses import asdict, fields, replace
from pathlib import Path
from .core import Options, atomic_json


def profile_data(name, options):
    if not isinstance(name,str) or not name.strip() or len(name)>80:
        raise ValueError('配置名称须为 1～80 个字符')
    options.validate()
    return {'schemaVersion':1,'kind':'VideoMergeCompress.compression','name':name.strip(),
            'options':asdict(replace(options,preset='custom'))}


def parse_profile(data):
    if not isinstance(data,dict) or data.get('schemaVersion')!=1 or data.get('kind')!='VideoMergeCompress.compression':
        raise ValueError('不是本软件支持的压缩配置（版本 1 JSON）')
    values=data.get('options')
    defaults=Options()
    allowed={f.name for f in fields(defaults)}
    if not isinstance(values,dict) or set(values)-allowed:
        raise ValueError('配置包含未知参数')
    for key,value in values.items():
        if type(value) is not type(getattr(defaults,key)):
            raise ValueError(f'配置参数类型错误：{key}')
    options=Options(**values)
    options.validate()
    clean=profile_data(data.get('name'), options)
    return clean['name'], Options(**clean['options'])


def read_profile(path):
    with Path(path).open('rb') as stream:
        raw=stream.read(65537)
    if len(raw)>65536:
        raise ValueError('配置文件超过 64 KiB')
    return parse_profile(json.loads(raw.decode('utf-8-sig')))


def save_profile(path,name,options):
    atomic_json(Path(path),profile_data(name,options))
