from sys import exit, stderr, stdout
from util import copytree

import os
import re
import requests
import subprocess
import pystache
import kpack
import errno

from resources import get_resource_root

class Project:
    def __init__(self, root=None):
        self.root = root
        if self.root == None: self.root = findroot()

    def __del__(self):
        pass

    def full_name(self):
        repo = self.get_config("repo")
        name = self.get_config("name")
        if not repo or not name:
            return None
        return repo + "/" + name

    def open(self, path, mode="r"):
        return open(os.path.join(self.root, path), mode=mode) # TODO: This leaks file descriptors

    def get_config(self, key, config="package.config"):
        lines = None
        try:
            with self.open(config) as c:
                lines = c.readlines()
        except:
            return None
        for line in lines:
            if line.startswith(key):
                try:
                    return line[line.index('=') + 1:].strip()
                except:
                    pass
        return None

    def set_config(self, key, value, config="package.config"):
        lines = None
        with self.open(config) as c:
            lines = c.readlines()
        found = False
        for i, line in enumerate(lines):
            if line.startswith(key):
                lines[i] = key + '=' + value + "\n"
                found = True
        if not found:
            lines.append("{0}={1}\n".format(key, value))
        if value == '':
            lines = [l for l in lines if not l.startswith(key)]
        with self.open("package.config", mode="w") as c:
            c.write(''.join(lines))

    def get_packages(self):
        deps = self.get_config("dependencies")
        if deps == None:
            deps = list()
        else:
            deps = deps.split(' ')
        for i, dep in enumerate(deps):
            if ':' in dep:
                deps[i] = dep.split(':')[0]
        return deps

    def get_implicit_packages(self, packages):
        extra = list()
        for package in packages:
            info = requests.get('https://packages.knightos.org/api/v1/' + package)
            if info.status_code == 404:
                stderr.write("Cannot find '{0}' on packages.knightos.org.\n".format(package))
                exit(1)
            elif info.status_code != 200:
                stderr.write("An error occured while contacting packages.knightos.org for information.\n")
                exit(1)
            for dep in info.json()['dependencies']:
                if not dep in extra and not dep in self.get_packages():
                    if dep == self.full_name():
                        print("Notice: this project fulfills the '{0}' dependency, skipping".format(dep))
                    else:
                        print("Adding dependency: " + dep)
                        extra.append(dep)
        return extra

    def gen_package_make(self):
        template_vars = { "packages": list() }
        for root, dirs, files in os.walk(os.path.join(self.root, ".knightos", "packages")):
            for package in files:
                info = kpack.PackageInfo.read_package(os.path.join(self.root, ".knightos", "packages", package))
                template_vars["packages"].append({ "name": info.name, "repo": info.repo, "filename": package })
        if os.path.exists(os.path.join(self.root, ".knightos", "pkgroot", "slib")):
            template_vars["libraries"] = list()
            for root, dirs, files in os.walk(os.path.join(self.root, ".knightos", "pkgroot", "slib")):
                for library in files:
                    template_vars["libraries"].append({ "path": os.path.join(self.root, ".knightos", "pkgroot", "slib", library) })
        with open(os.path.join(get_resource_root(), "templates", "packages.make"), "r") as ofile:
            path = os.path.join(self.root, ".knightos", "packages.make")
            with open(os.path.join(path), "w") as file:
                file.write(pystache.render(ofile.read(), template_vars))

    def install(self, packages, site_only, init=False, link=False):
        if len(packages) == 0 and os.path.exists(os.path.join(packages[0], "package.config")):
            # TODO: Install local package
            pass

        deps = self.get_packages()
        extra = self.get_implicit_packages(packages)
        all_packages = extra + packages
        all_packages = [p for p in all_packages if p != self.full_name()]
        files = []
        # Download packages
        for p in all_packages:
            stdout.write("Downloading {0}".format(p))
            r = requests.get('https://packages.knightos.org/api/v1/' + p)
            path = os.path.join(self.root, ".knightos", "packages", "{0}-{1}.pkg".format(r.json()['name'], r.json()['version']))
            files.append(path)
            with self.open(path, mode="wb") as fd:
                _r = requests.get('https://packages.knightos.org/{0}/download'.format(r.json()['full_name']))
                total = int(_r.headers.get('content-length'))
                length = 0
                for chunk in _r.iter_content(1024):
                    fd.write(chunk)
                    length += len(chunk)
                    if stdout.isatty():
                        stdout.write("\rDownloading {:<20} {:<20}".format(p, str(int(length / total * 100)) + '%'))
            stdout.write("\n")
            # Initial extraction
            FNULL = open(os.devnull, 'w')
            subprocess.call(["kpack", "-e", path, os.path.join(self.root, ".knightos", "pkgroot")], stdout=FNULL, stderr=subprocess.STDOUT)
            subprocess.call(["kpack", "-e", "-s", path, os.path.join(self.root, ".knightos", "pkgroot")], stdout=FNULL, stderr=subprocess.STDOUT)
        if not site_only:
            for package in packages:
                deps.append(package)
        if not init:
            self.set_config("dependencies", " ".join(deps))
        if link:
            force_symlink(os.path.join("bin", "castle"), os.path.join(self.root, ".knightos", "pkgroot", "bin", "launcher"))
            force_symlink(os.path.join("bin", "threadlist"), os.path.join(self.root, ".knightos", "pkgroot", "bin", "switcher"))
            force_symlink(os.path.join("bin", "fileman"), os.path.join(self.root, ".knightos", "pkgroot", "bin", "browser"))

        # Install packages
        self.gen_package_make()
        return all_packages

def findroot():
    path = os.getcwd()
    while path != "/": # TODO: Confirm this is cross platform
        if ".knightos" in os.listdir(path):
            return path
        else:
            path = os.path.realpath(os.path.join(path, ".."))
    stderr.write("There doesn't seem to be a KnightOS project here. Did you run `knightos init`?\n")
    exit(1)

#Currently there's no way to overwrite a pre-existing symlink
def force_symlink(file1, file2):
    try:
        os.symlink(file1, file2)
    except OSError as e:
        if e.errno == errno.EEXIST:
            os.remove(file2)
            os.symlink(file1, file2)
