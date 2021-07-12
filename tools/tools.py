from sklearn.neighbors import NearestNeighbors
import numpy as np
import glob
import laspy
from sklearn.neighbors import NearestNeighbors
from multiprocessing import Pool, get_context
import pandas as pd
import os
import shutil
from sklearn.cluster import DBSCAN
from scipy.interpolate import griddata
from copy import deepcopy
import hdbscan
from multiprocessing import get_context
from scipy import spatial


def make_folder_structure(filename):
    filename = filename.replace('\\', '/')
    directory = os.path.dirname(os.path.realpath(filename)) + '/'
    filename = filename.split('/')[-1][:-4]
    output_dir = directory + filename+'_FSCT_output/'
    working_dir = directory + filename+'_FSCT_output/working_directory/'

    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)

    if not os.path.isdir(working_dir):
        os.makedirs(working_dir)
    else:
        shutil.rmtree(working_dir, ignore_errors=True)
        os.makedirs(working_dir)

    return output_dir, working_dir


def subsample(args):
    X, min_spacing = args
    neighbours = NearestNeighbors(n_neighbors=2, algorithm='kd_tree', metric='euclidean').fit(X[:, :3])
    distances, indices = neighbours.kneighbors(X[:, :3])
    X_keep = X[distances[:, 1] >= min_spacing]
    i1 = [distances[:, 1] < min_spacing][0]
    i2 = [X[indices[:, 0], 2] < X[indices[:, 1], 2]][0]
    X_check = X[np.logical_and(i1, i2)]

    while np.shape(X_check)[0] > 1:
        neighbours = NearestNeighbors(n_neighbors=2, algorithm='kd_tree', metric='euclidean').fit(X_check[:, :3])
        distances, indices = neighbours.kneighbors(X_check[:, :3])
        X_keep = np.vstack((X_keep, X_check[distances[:, 1] >= min_spacing, :]))
        i1 = [distances[:, 1] < min_spacing][0]
        i2 = [X_check[indices[:, 0], 2] < X_check[indices[:, 1], 2]][0]
        X_check = X_check[np.logical_and(i1, i2)]
    return X_keep


def subsample_point_cloud(pointcloud, min_spacing, num_procs=1):
    """
    Args:
        pointcloud: The input point cloud.
        min_spacing: The minimum allowable distance between two points in the point cloud.
        num_procs: Number of threads to use when subsampling.

    Returns:
        pointcloud: The subsampled point cloud.
    """
    print("Subsampling...")
    print("Original number of points:", pointcloud.shape[0])

    if num_procs > 1:
        num_slices = num_procs
        Xmin = np.min(pointcloud[:, 0])
        Xmax = np.max(pointcloud[:, 0])
        Xrange = Xmax - Xmin
        slice_list = []
        kdtree = spatial.cKDTree(np.atleast_2d(pointcloud[:, 0]).T, leafsize=10000)
        for i in range(num_slices):
            min_bound = Xmin + i*(Xrange/num_slices)
            results = kdtree.query_ball_point(np.array([min_bound]), r=Xrange/num_slices)
            # mask = np.logical_and(pointcloud[:, 0] >= min_bound, pointcloud[:, 0] < max_bound)
            pc_slice = pointcloud[results]
            print("Slice size:", pc_slice.shape[0], '    Slice number:', i+1, '/', num_slices)
            slice_list.append([pc_slice, min_spacing])

        pointcloud = np.zeros((0, pointcloud.shape[1]))
        with get_context("spawn").Pool(processes=num_procs) as pool:
            for i in pool.imap_unordered(subsample, slice_list):
                pointcloud = np.vstack((pointcloud, i))

    else:
        pointcloud = subsample([pointcloud, min_spacing])

    print("Subsampled number of points:", pointcloud.shape[0])
    return pointcloud


def load_file(filename, plot_centre=None, plot_radius=0, plot_radius_buffer=0, silent=False, headers_of_interest=None, return_num_points=False):
    output_dir = os.path.dirname(os.path.realpath(filename)).replace('\\', '/') + '/' + filename.split('/')[-1][:-4] + '_FSCT_output/'

    if headers_of_interest is None:
        headers_of_interest = []
    if not silent:
        print('Loading file...', filename)
    file_extension = filename[-4:]
    coord_headers = ['x', 'y', 'z']
    output_headers = []

    if file_extension == '.las' or file_extension == '.laz':
        inFile = laspy.read(filename)
        header_names = list(inFile.point_format.dimension_names)
        pointcloud = np.vstack((inFile.x, inFile.y, inFile.z))
        if len(headers_of_interest) != 0:
            headers_of_interest = headers_of_interest[3:]
            for header in headers_of_interest:
                if header in header_names:
                    pointcloud = np.vstack((pointcloud, getattr(inFile, header)))
                    output_headers.append(header)
        pointcloud = pointcloud.transpose()

    elif file_extension == '.csv':
        pointcloud = np.array(pd.read_csv(filename, header=None, index_col=None, delim_whitespace=True))

    original_num_points = pointcloud.shape[0]

    if plot_centre is None:
        plot_centre = np.mean(pointcloud[:, :2], axis=0)

    if plot_radius > 0:
        distances = np.linalg.norm(pointcloud[:, :2] - plot_centre, axis=1)
        keep_points = distances < plot_radius + plot_radius_buffer
        pointcloud = pointcloud[keep_points]
    if return_num_points:
        return pointcloud, coord_headers + output_headers, original_num_points
    else:
        return pointcloud, coord_headers + output_headers


def save_file(filename, pointcloud, headers_of_interest=None, silent=False):
    print(headers_of_interest)
    if headers_of_interest is None:
        headers_of_interest = []
    if pointcloud.shape[0] == 0:
        print(filename, 'is empty...')
    else:
        if not silent:
            print('Saving file...')
        if filename[-4:] == '.las':
            las = laspy.create(file_version="1.4", point_format=7)
            las.header.offsets = np.min(pointcloud[:, :3], axis=0)
            las.header.scales = [0.001, 0.001, 0.001]

            las.x = pointcloud[:, 0]
            las.y = pointcloud[:, 1]
            las.z = pointcloud[:, 2]

            if len(headers_of_interest) != 0:
                headers_of_interest = headers_of_interest[3:]

                #  The reverse step below just puts the headings in the preferred order. They are backwards without it.
                col_idxs = list(range(3, pointcloud.shape[1]))
                headers_of_interest.reverse()

                col_idxs.reverse()
                for header, i in zip(headers_of_interest, col_idxs):
                    column = pointcloud[:, i]
                    if header in ['red', 'green', 'blue']:
                        setattr(las, header, column)
                    else:
                        las.add_extra_dim(laspy.ExtraBytesParams(name=header, type="f8"))
                        setattr(las, header, column)
            las.write(filename)
            if not silent:
                print("Saved to:", filename)

        elif filename[-4:] == '.csv':
            pd.DataFrame(pointcloud).to_csv(filename, header=None, index=None, sep=' ')
            print("Saved to:", filename)


def get_heights_above_DTM(points, DTM):
    grid = griddata((DTM[:, 0], DTM[:, 1]), DTM[:, 2], points[:, 0:2], method='linear',
                    fill_value=np.median(DTM[:, 2]))
    points[:, -1] = points[:, 2] - grid
    return points


def clustering(points, eps=0.05, min_samples=2, n_jobs=1, mode='DBSCAN'):
    print("Clustering")
    assert mode == 'DBSCAN' or mode == 'HDBSCAN'

    if mode == 'HDBSCAN':
        cluster_labels = hdbscan.HDBSCAN(min_cluster_size=100).fit_predict(points[:, :3])
        return np.hstack((points, np.atleast_2d(cluster_labels).T))

    elif mode == 'DBSCAN':
        db = DBSCAN(eps=eps, min_samples=min_samples, metric='euclidean', algorithm='kd_tree', n_jobs=n_jobs).fit(points[:, :3])

        return np.hstack((points, np.atleast_2d(db.labels_).T))


def low_resolution_hack_mode(point_cloud, num_iterations, min_spacing, num_procs):
    print('Using low resolution point cloud hack mode...')
    print('Original point cloud shape:', point_cloud.shape)
    point_cloud_original = deepcopy(point_cloud)
    for i in range(num_iterations):
        duplicated = deepcopy(point_cloud_original)

        duplicated[:, :3] = duplicated[:, :3] + np.hstack(
                (np.random.normal(-0.025, 0.025, size=(duplicated.shape[0], 1)),
                 np.random.normal(-0.025, 0.025, size=(duplicated.shape[0], 1)),
                 np.random.normal(-0.025, 0.025, size=(duplicated.shape[0], 1))))
        point_cloud = np.vstack((point_cloud, duplicated))
        point_cloud = subsample_point_cloud(point_cloud, min_spacing, num_procs)
    print('Hacked point cloud shape:', point_cloud.shape)
    return point_cloud
