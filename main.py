import hashlib
import os
import re
import shutil
import tempfile
from urllib.request import Request, urlopen

import requests
import subprocess

from loguru import logger
import yaml
from tqdm import tqdm


def download_url_to_file(url, dst, hash_prefix=None, progress=True):
    file_size = None
    req = Request(url, headers={"User-Agent": "python"})
    u = urlopen(req)
    meta = u.info()
    if hasattr(meta, 'getheaders'):
        content_length = meta.getheaders("Content-Length")
    else:
        content_length = meta.get_all("Content-Length")
    if content_length is not None and len(content_length) > 0:
        file_size = int(content_length[0])

    # We deliberately save it in a temp file and move it after
    # download is complete. This prevents a local working checkpoint
    # being overridden by a broken download.
    dst = os.path.expanduser(dst)
    dst_dir = os.path.dirname(dst)
    f = tempfile.NamedTemporaryFile(delete=False, dir=dst_dir)

    try:
        if hash_prefix is not None:
            sha256 = hashlib.sha256()
        with tqdm(total=file_size, disable=not progress,
                  unit='B', unit_scale=True, unit_divisor=1024) as pbar:
            while True:
                buffer = u.read(8192)
                if len(buffer) == 0:
                    break
                f.write(buffer)
                if hash_prefix is not None:
                    sha256.update(buffer)
                pbar.update(len(buffer))

        f.close()
        if hash_prefix is not None:
            digest = sha256.hexdigest()
            if digest[:len(hash_prefix)] != hash_prefix:
                raise RuntimeError('invalid hash value (expected "{}", got "{}")'
                                   .format(hash_prefix, digest))
        shutil.move(f.name, dst)
    finally:
        f.close()
        if os.path.exists(f.name):
            os.remove(f.name)


class ProgramInstaller:
    def __init__(self, config, installed_file):
        if isinstance(config, str) and os.path.isfile(config):
            with open(config, encoding='utf-8') as f:
                config = yaml.safe_load(f)

        self.config = config
        logger.info(config)
        self.root = config.get('base', {}).get('root', '')
        self.cache = config.get('base', {}).get('cache', '')
        self.programs = config.get('programs', [])
        self.installed_file = installed_file

    def is_installed(self, name, version):
        with open(self.installed_file, 'r') as file:
            for line in file:
                installed_name, installed_version = line.strip().split(',')
                if installed_name == name and installed_version == version:
                    return True
        return False

    def install_program(self, program):
        name = program['name']
        version = program['version']
        url = program['url']

        # 处理版本为 "latest" 的情况
        if version.lower() == 'latest':
            latest_version = self.get_latest_version(url, program['latest_version_page'], program['latest_version_re'])
            if latest_version:
                version = latest_version
                program['version'] = version
            else:
                raise Exception(f"无法获取{name}程序的最新版本号")

        # 检查目标程序是否已经安装
        if self.is_installed(name, version):
            logger.info(f"程序 {name} 的版本 {version} 已经存在，跳过安装")
            return

        download_url = self._modify_param(program['download_url'], program)

        installer_params = program.get('installer_params', [])

        modified_params = []
        for param in installer_params:
            param = self._modify_param(param, program)
            modified_params.append(param)

        # 构建缓存文件路径
        cache_path = os.path.join(self.cache, os.path.basename(download_url))

        if not os.path.exists(cache_path):
            # 缓存文件不存在，下载程序到缓存目录
            self.download_file(download_url, cache_path)
        else:
            logger.info('using cache')
        # 安装程序到指定目录
        # target_dir = os.path.join(self.root, name, f'{name}{version}')  # 安装目标路径
        logger.info('silent_config',program.get('silent_config'))

        if program.get('silent_config'):
            with open(os.path.join(self.cache, 'silent_config'),'w') as f:
                for line in program.get('silent_config'):
                    f.write(line+'\n')

        self.install_from_cache(cache_path, modified_params)
        # 将已安装的程序信息写入 installed_file 文件
        with open(self.installed_file, 'a') as file:
            file.write(f"{name},{version}\n")

    def _modify_param(self, param, program):
        for match in re.findall('\{.*?\}', param):
            variable = match[1:-1]
            logger.info(variable)
            if variable in program:
                value = program[variable]
                param = param.replace(match, value)
            elif '.' in variable:
                value = self.config
                for p in variable.split('.'):
                    value = value.get(p)
                param = param.replace(match, value)
        logger.info(param)
        return param

    def download_file(self, url, filepath):
        download_url_to_file(url, filepath)

    def install_from_cache(self, cache_filepath, installer_params):
        # 替换参数中的占位符
        cmd = [cache_filepath] + installer_params
        logger.info('installing')
        subprocess.run(cmd, check=True)

    def get_latest_version(self, url, page, pattern):
        if not page or not pattern:
            return None

        response = requests.get(url + page)
        match = re.search(pattern, response.text)
        if match:
            return match.group(1)
        else:
            return None

    def install_all_programs(self):
        for program in self.programs:
            self.install_program(program)


ins = ProgramInstaller('requirements.yaml', 'installed.txt')
ins.install_all_programs()
