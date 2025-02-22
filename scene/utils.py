from __future__ import print_function

import os
import math
import json
import logging
import numpy as np
from PIL import Image
from datetime import datetime
import imageio
from glob import glob
import shutil
import matplotlib.pyplot as plt

def prepare_dirs_and_logger(config):
    os.chdir(os.path.dirname(__file__))

    formatter = logging.Formatter("%(asctime)s:%(levelname)s::%(message)s")
    logger = logging.getLogger()

    for hdlr in logger.handlers:
        logger.removeHandler(hdlr)

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    logger.addHandler(handler)

    # data path
    config.data_path = os.path.join(config.data_dir, config.dataset)

    # model path
    if config.load_path:
        config.model_dir = config.load_path
    elif not hasattr(config, 'model_dir'):    
        model_name = "{}/{}/{}_{}_{}".format(
            'ae', config.data_type, config.dataset, get_time(), config.tag)

        config.model_dir = os.path.join(config.log_dir, model_name)
    
    if not os.path.exists(config.model_dir):
        os.makedirs(config.model_dir)

def get_time():
    return datetime.now().strftime("%m%d_%H%M%S")

def save_config(config):
    param_path = os.path.join(config.model_dir, "params.json")

    print("[*] MODEL dir: %s" % config.model_dir)
    print("[*] PARAM path: %s" % param_path)

    with open(param_path, 'w') as fp:
        json.dump(config.__dict__, fp, indent=4, sort_keys=True)

def rank(array):
    return len(array.shape)

def make_grid(tensor, nrow=8, padding=2,
              normalize=False, scale_each=False):
    """Code based on https://github.com/pytorch/vision/blob/master/torchvision/utils.py"""
    nmaps = tensor.shape[0]
    xmaps = min(nrow, nmaps)
    ymaps = int(math.ceil(float(nmaps) / xmaps))
    height, width = int(tensor.shape[1] + padding), int(tensor.shape[2] + padding)
    if padding == 0:
        grid = np.zeros([height * ymaps, width * xmaps, 3], dtype=np.uint8)
    else:
        grid = np.zeros([height * ymaps + 1 + padding // 2, width * xmaps + 1 + padding // 2, 3], dtype=np.uint8)
    k = 0
    for y in range(ymaps):
        for x in range(xmaps):
            if k >= nmaps:
                break
            if padding == 0:
                h, h_width = y * height, height
                w, w_width = x * width, width
            else:
                h, h_width = y * height + 1 + padding // 2, height - padding
                w, w_width = x * width + 1 + padding // 2, width - padding

            grid[h:h+h_width, w:w+w_width] = tensor[k]
            k = k + 1
    return grid

def save_image(tensor, filename, nrow=8, padding=2,
               normalize=False, scale_each=False, single=False):
    if not single:
        ndarr = make_grid(tensor, nrow=nrow, padding=padding,
                                normalize=normalize, scale_each=scale_each)
    else:
        h, w = tensor.shape[0], tensor.shape[1]
        ndarr = np.zeros([h,w,3], dtype=np.uint8)
        ndarr[:,:] = tensor[:,:]
        
    im = Image.fromarray(ndarr)
    im.save(filename)

def streamplot(x, filename, density=2.0, scale=5.0):
    # uv: [y,x,2]
    u = x[::-1,:,0]
    v = x[::-1,:,1]

    h, w = x.shape[0], x.shape[1]
    y0, y1 = (0,h-1)
    x0, x1 = (0,w-1)
    Y, X = np.ogrid[y0:y1:complex(0,h), x0:x1:complex(0,w)]
    speed = np.sqrt(u*u + v*v)
    lw = 2*speed / speed.max() + 0.5
    # color = speed / speed.max()
    color = 'k'

    fig, ax = plt.subplots()
    fig.set_size_inches(w*0.01*scale,h*0.01*scale)
    fig.frameon = False
    ax.set_axis_off()    
    ax.streamplot(X, Y, u, v, color=color, linewidth=lw,
        density=density, arrowstyle='->', arrowsize=1.0)

    ax.set_aspect('equal')
    ax.figure.subplots_adjust(bottom=0, top=1, left=0, right=1)
    ax.xaxis.set_ticks([])
    ax.yaxis.set_ticks([])
    ax.axes.get_xaxis().set_visible(False)
    ax.axes.get_yaxis().set_visible(False)

    fig.savefig(filename, bbox_inches='tight')

    # If we haven't already shown or saved the plot, then we need to
    # draw the figure first...
    fig.canvas.draw()

    # Now we can save it to a numpy array.
    data = np.fromstring(fig.canvas.tostring_rgb(), dtype=np.uint8, sep='')
    data = data.reshape(fig.canvas.get_width_height()[::-1] + (3,))
    plt.close()

    return data

def vortplot(x, filename):
    dudx = x[1:,1:,0] - x[1:,:-1,0]
    dvdx = x[1:,1:,1] - x[1:,:-1,1]
    dudy = x[:-1,:-1,0] - x[1:,:-1,0] # horizontally flipped
    dvdy = x[:-1,:-1,1] - x[1:,:-1,1] # horizontally flipped
    dudx = dudx[2:-2,2:-2]
    dvdx = dvdx[2:-2,2:-2]
    dudy = dudy[2:-2,2:-2]
    dvdy = dvdy[2:-2,2:-2]
    x_ = dvdx - dudy
    
    vrange = [np.abs(x_.min()), x_.max()]
    x_[x_>0] /= vrange[1]
    x_[x_<0] /= vrange[0]
    x_ = (x_+1)*0.5 # [0,1]
    x_ = np.uint8(plt.cm.RdBu(x_)*255)
    im = Image.fromarray(x_)
    im.save(filename)
    return x_

def gradplot(x, filename):
    dudx = x[1:,1:,0] - x[1:,:-1,0]
    dudy = x[:-1,:-1,0] - x[1:,:-1,0] # horizontally flipped
    dudx = dudx[2:-2,2:-2]
    dudy = dudy[2:-2,2:-2]
    x_ = dudx**2 + dudy**2    
    print(filename, x.min(), x.max(), x_.max())
    x_ /= 0.708149 
    x_ = np.uint8(plt.cm.viridis(x_)*255)
    im = Image.fromarray(x_)
    im.save(filename)

    x = x[:,:,0]
    vrange = [np.abs(x.min()), x.max()]
    x[x>0] /= vrange[1]
    x[x<0] /= vrange[0]
    x = (x+1)*0.5 # [0,1]
    x = np.uint8(plt.cm.viridis(x)*255)
    im = Image.fromarray(x)

    filename = filename[:-8] + 's' + filename[-8:]
    im.save(filename)
    return x_

def jacoplot(x, filename):
    dudx = x[1:,1:,0] - x[1:,:-1,0]
    dvdx = x[1:,1:,1] - x[1:,:-1,1]
    dudy = x[:-1,:-1,0] - x[1:,:-1,0] # horizontally flipped
    dvdy = x[:-1,:-1,1] - x[1:,:-1,1] # horizontally flipped
    dudx = dudx[2:-2,2:-2]
    dvdx = dvdx[2:-2,2:-2]
    dudy = dudy[2:-2,2:-2]
    dvdy = dvdy[2:-2,2:-2]
    x_ = dudx**2 + dudy**2 + dvdx**2 + dvdy**2
    print(filename, x_.max())
    x_ /= 11.3974
    x_ = np.uint8(plt.cm.viridis(x_)*255)
    im = Image.fromarray(x_)
    im.save(filename)

    x = x[:,:,0]**2 + x[:,:,1]**2
    x /= x.max()
    x = np.uint8(plt.cm.viridis(x)*255)
    im = Image.fromarray(x)

    filename = filename[:-8] + 's' + filename[-8:]
    im.save(filename)
    return x_

def convert_png2mp4(imgdir, filename, fps, delete_imgdir=False):
    dirname = os.path.dirname(filename)
    if not os.path.exists(dirname):
        os.makedirs(dirname)

    try:
        writer = imageio.get_writer(filename, fps=fps)
    except Exception:
        imageio.plugins.ffmpeg.download()
        writer = imageio.get_writer(filename, fps=fps)

    imgs = sorted(glob("{}/*.png".format(imgdir)))
    for img in imgs:
        im = imageio.imread(img)
        writer.append_data(im)
    
    writer.close()
    
    if delete_imgdir: shutil.rmtree(imgdir)
    
def rf(o, k, stride): # input size from output size
    return (o-1)*stride + k

def receptive_field_size(c, k, s):
    if c == 0:
        return rf(rf(1, k, 1), k, 1)
    else:
        rfs = receptive_field_size(c-1, k, s)
        print('%d: %d' % (c-1, rfs))
        return rf(rfs, k, s)

if __name__ == '__main__':
    c, k, s = 4, 3, 2
    rfs = receptive_field_size(c, k, s)
    print('c{}k{}s{} receptive field size'.format(c, k, s), rfs)

    c, k = 3, 3
    rfs = receptive_field_size(c, k, s)
    print('c{}k{}s{} receptive field size'.format(c, k, s), rfs)

    c, k = 5, 3
    rfs = receptive_field_size(c, k, s)
    print('c{}k{}s{} receptive field size'.format(c, k, s), rfs)

    c, k = 4, 4
    rfs = receptive_field_size(c, k, s)
    print('c{}k{}s{} receptive field size'.format(c, k, s), rfs)

    c, k = 3, 4
    rfs = receptive_field_size(c, k, s)
    print('c{}k{}s{} receptive field size'.format(c, k, s), rfs)

