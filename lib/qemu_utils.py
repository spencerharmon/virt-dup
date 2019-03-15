from lib.exceptions.qemu_exceptions import BlockCommitException
import subprocess
import json


def block_commit(top, objectdef=None, image_opts=False, q=True, fmt=None, cache=None, base=None, d=False, p=False):
    qemu_img_commit = ["qemu-img", "commit"]
    if objectdef is not None:
        qemu_img_commit.append(f"--object")
        qemu_img_commit.append(objectdef)
    if image_opts is True:
        qemu_img_commit.append("--image-opts")
    if q is True:
        qemu_img_commit.append("-q")
    if fmt is not None:
        qemu_img_commit.append(f"-f")
        qemu_img_commit.append(fmt)
    if cache is not None:
        qemu_img_commit.append(f"-t")
        qemu_img_commit.append(cache)
    if base is not None:
        qemu_img_commit.append(f"-b")
        qemu_img_commit.append(base)
    if d is True:
        qemu_img_commit.append("-d")
    if p is True:
        qemu_img_commit.append("-p")
    qemu_img_commit.append(top)

    out = subprocess.run(qemu_img_commit, capture_output=True)
    if out.returncode != 0:
        raise BlockCommitException(out.stderr)
    return out.stdout, out.stderr


def img_info(filename, objectdef=None, image_opts=False, fmt=None, backing_chain=True, U=False):
    '''
    convenience function for `qemu-img info`
    All options are supported except '--output=' because the only acceptable output is json.
    :param filename: 
    :param objectdef: 
    :param image_opts: 
    :param fmt: 
    :return: dict
    '''
    qemu_img_info = ["qemu-img", "info", "--output=json"]
    if objectdef is not None:
        qemu_img_info.append(f"--object")
        qemu_img_info.append(objectdef)
    if image_opts:
        qemu_img_info.append("--img-opts")
    if fmt is not None:
        qemu_img_info.append("-f")
        qemu_img_info.append(fmt)
    if backing_chain:
        qemu_img_info.append("--backing-chain")
    if U:
        qemu_img_info.append("-U")
    qemu_img_info.append(filename)
    out = subprocess.run(qemu_img_info, capture_output=True)
    ret = json.loads(out.stdout)
    return ret
