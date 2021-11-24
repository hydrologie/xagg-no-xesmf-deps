import xarray as xr
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Polygon
import warnings
import xesmf as xe

from . aux import (find_rel_area,fix_ds,get_bnds,subset_find,list_or_first)
from . classes import (weightmap,aggregated)


def process_weights(ds,weights=None,target='ds'):
    """ Process weights - including regridding
    
    If ``target == 'ds'``, regrid `weights` to `ds`. If ``target == 'weights'``,
    regrid `ds` to `weights`. 

    Parameters
    ---------------
    ds : :class:`xarray.Dataset`, :class:`xarray.DataArray`
        an :class:`xarray.Dataset`/:class:`xarray.DataArray` to regrid
    
    weights : :class:`xarray.DataArray`, optional, default = ``None``
        an :class:`xarray.DataArray` containing a weight (numeric) 
        at each location
    
    target : str, optional, default = ``'ds'``
        whether weights should be regridded to the `ds` grid (by
        default) or vice-versa (not yet supported, returns 
        `NotImplementedError`)


    Returns
    ---------------
    ds : :class:`xarray.Dataset`, :class:`xarray.DataArrays`
        the input :class:`xarray.Dataset`/:class:`xarray.DataArray`, with a new variable 
        `weights` specifying weights for each pixel

    weights_info : dict 
        a dictionary storing information about the 
        weights regridding process, with the fields:

        - ``target``: showing which of the two grids was retained
        - ``ds_grid``: a dictionary with the grid ``{"lat":ds.lat,"lon",ds.lon}``
        - ``weights_grid``: a dictionary with the grid ``{"lat":weights.lat,"lon":weights.lon}``
    """
    
    if weights is None:
        # (for robustness against running this without an extra if statement
        # in a wrapper function)
        weights_info = 'nowghts'
    else:
        # Check types
        if type(weights) is not xr.core.dataarray.DataArray:
            raise TypeError('[weights] must be an xarray DataArray.')
        if type(ds) not in [xr.core.dataarray.DataArray,
                                xr.core.dataset.Dataset]:
            raise TypeError('[ds] must be an xarray structure (DataArray or Dataset)')
            
        # Stick weights into the same supported input format as ds
        weights = fix_ds(weights)
        
        # Set regridding info
        weights_info = {'target':target,
                        'ds_grid':{'lat':ds.lat,'lon':ds.lon},
                        'weights_grid':{'lat':weights.lat,'lon':weights.lon}}

        # Regrid, if necessary (do nothing if the grids match up to within
        # floating-point precision)
        if ((not ((ds.sizes['lat'] is weights.sizes['lat']) & (ds.sizes['lon'] == weights.sizes['lon']))) or 
            (not (np.allclose(ds.lat,weights.lat) & np.allclose(ds.lon,weights.lon)))):
            if target == 'ds':
                print('regridding weights to data grid...')
                # Create regridder to the [ds] coordinates
                rgrd = xe.Regridder(weights,ds,'bilinear')
                # Regrid [weights] to [ds] grids
                weights = rgrd(weights)

            elif target == 'weights':
                raise NotImplementedError('The '+target+' variable is not *yet* supported as a target for regridding. Please choose "ds" for now.')
                # This is because of lack of downstream capability right now... 
                print('regridding data to weights grid...')
                # Create regridder to the [weights] coordinates
                rgrd = xe.Regridder(ds,weights,'bilinear')
                # Regrid [ds] to [weights] grid
                ds = rgrd(ds)

            else:
                raise KeyError(target+' is not a supported target for regridding. Choose "weights" or "ds".')
            
        # Add weights to ds
        ds['weights'] = weights
            
    # Return
    return ds,weights_info


def create_raster_polygons(ds,
                           mask=None,subset_bbox=None,
                           weights=None,weights_target='ds'):
    """ Create polygons for each pixel in a raster

    Note: 
    'lat_bnds' and 'lon_bnds' can be created through the
    :func:`xagg.aux.get_bnds` function if they are not already included
    in the input raster file. 
    
    Note:
    Currently this code only supports regular 
    rectangular grids (so where every pixel side is
    a straight line in lat/lon space). Future versions
    may include support for irregular grids. 
    
    Parameters
    ---------------
    ds : :class:`xarray.Dataset`
        an xarray dataset with the variables 
        'lat_bnds' and 'lon_bnds', which are both
        lat/lon x 2 arrays giving the min and 
        max values of lat and lon for each pixel
        given by lat/lon
    subset_bbox : :class:`geopandas.GeoDataFrame`, optional, default = ``None`` 
        if a :class:`geopandas.GeoDataFrame`
        is entered, the bounding
        box around the geometries in the `gdf` 
        are used to mask the grid, to reduce
        the number of pixel polygons created
          
    Returns
    ---------------
    pix_agg: dict
        a dictionary containing:

        - ``'gdf_pixels'`` 
            a :class:`geopandas.GeoDataFrame` containing a 
            'geometry' giving the pixel boundaries 
            for each 'lat' / 'lon' pair
        - ``'source_grid'`` 
            a dictionary containing the original
            lat and lon inputs under the keys
            "lat" and "lon" (just the 
            :class:`xarray.DataArray` of those variables in
            the input `ds`)
                      
    """
    
    # Standardize inputs
    ds = fix_ds(ds)
    ds = get_bnds(ds)
    #breakpoint()
    # Subset by shapefile bounding box, if desired
    if subset_bbox is not None:
        if type(subset_bbox) == gpd.geodataframe.GeoDataFrame:
            # Using the biggest difference in lat/lon to make sure that the pixels are subset
            # in a way that the bounding box is fully filled out
            bbox_thresh = np.max([ds.lat.diff('lat').max(),ds.lon.diff('lon').max()])+0.1
            ds = ds.sel(lon=slice(subset_bbox.total_bounds[0]-bbox_thresh,subset_bbox.total_bounds[2]+bbox_thresh),
                        lat=slice(subset_bbox.total_bounds[1]-bbox_thresh,subset_bbox.total_bounds[3]+bbox_thresh))
        else:
            warnings.warn('[subset_bbox] is not a geodataframe; no mask by polygon bounding box used.')
            
    # Process weights
    ds,winf = process_weights(ds,weights,target=weights_target)
            
    # Mask
    if mask is not None:
        raise NotImplementedError('Masking by grid not yet supported. Stay tuned...')
        
    # Create dataset which has a lat/lon bound value for each individual pixel, 
    # broadcasted out over each lat/lon pair
    (ds_bnds,) = (xr.broadcast(ds.isel({d:0 for d in [k for k in ds.dims.keys() if k not in ['lat','lon','bnds']]}).
                              drop_vars([v for v in ds.keys() if v not in ['lat_bnds','lon_bnds']])))
    # Stack so it's just pixels and bounds
    ds_bnds = ds_bnds.stack(loc=('lat','lon'))
    
    # In order:
    # (lon0,lat0),(lon0,lat1),(lon1,lat1),(lon1,lat1), but as a single array; to be 
    # put in the right format for Polygon in the next step
    pix_poly_coords = np.transpose(np.vstack([ds_bnds.lon_bnds.isel(bnds=0).values,ds_bnds.lat_bnds.isel(bnds=0).values,
                                                ds_bnds.lon_bnds.isel(bnds=0).values,ds_bnds.lat_bnds.isel(bnds=1).values,
                                                ds_bnds.lon_bnds.isel(bnds=1).values,ds_bnds.lat_bnds.isel(bnds=1).values,
                                                ds_bnds.lon_bnds.isel(bnds=1).values,ds_bnds.lat_bnds.isel(bnds=0).values]))
    
    # Reshape so each location has a 4 x 2 (vertex vs coordinate) array, 
    # and convert each of those vertices to tuples. This means every element
    # of pix_poly_coords is the input to shapely.geometry.Polygon of one pixel
    pix_poly_coords = tuple(map(tuple,np.reshape(pix_poly_coords,(np.shape(pix_poly_coords)[0],4,2))))
    
    # Create empty geodataframe
    gdf_pixels = gpd.GeoDataFrame()
    gdf_pixels['lat'] = [None]*ds_bnds.dims['loc']
    gdf_pixels['lon'] = [None]*ds_bnds.dims['loc']
    gdf_pixels['geometry'] = [None]*ds_bnds.dims['loc']
    if weights is not None:
        # Stack weights so they are linearly indexed like the ds (and fill
        # NAs with 0s)
        weights = ds.weights.stack(loc=('lat','lon')).fillna(0)
        # Preallocate weights column
        gdf_pixels['weights'] = [None]*ds_bnds.dims['loc']
    
    # Now populate with a polygon for every pixel, and the lat/lon coordinates
    # of that pixel (Try if preallocating it with the right dimensions above 
    # makes it faster, because it's pretty slow rn (NB: it doesn't really))
    for loc_idx in np.arange(0,ds_bnds.dims['loc']):
        gdf_pixels.loc[loc_idx,'lat'] = ds_bnds.lat.isel(loc=loc_idx).values
        gdf_pixels.loc[loc_idx,'lon'] = ds_bnds.lon.isel(loc=loc_idx).values
        gdf_pixels.loc[loc_idx,'geometry'] = Polygon(pix_poly_coords[loc_idx])
        if weights is not None:
            gdf_pixels.loc[loc_idx,'weights'] = weights.isel(loc=loc_idx).values
        
    # Add a "pixel idx" to make indexing better later
    gdf_pixels['pix_idx'] = gdf_pixels.index.values
    
    # Add crs (normal lat/lon onto WGS84)
    gdf_pixels = gdf_pixels.set_crs("EPSG:4326")
    #gdf_pixels.crs = {'init':'EPSG:4326'}
    
    # Save the source grid for further reference
    source_grid = {'lat':ds_bnds.lat,'lon':ds_bnds.lon}
    
    pix_agg = {'gdf_pixels':gdf_pixels,'source_grid':source_grid}
    
    # Return the created geodataframe
    return pix_agg


def get_pixel_overlaps(gdf_in,pix_agg):
    """ Get, for each polygon, the pixels that overlap and their area of overlap
    
    Finds, for each polygon in `gdf_in`, which pixels intersect it, and by how much. 
    
    Note: 
    Uses EASE-Grid 2.0 on the WGS84 datum to calculate relative areas
    (see https://nsidc.org/data/ease)
    
    Parameters
    ---------------
    gdf_in : :class:`geopandas.GeoDataFrane`
        a :class:`geopandas.GeoDataFrame` giving the polygons over which 
        the variables should be aggregated. Can be just a read
        shapefile (with the added column of "poly_idx", which 
        is just the index as a column).
    pix_agg : dict
        the output of :func:`xagg.core.create_raster_polygons`; a dict containing:

        - ``'gdf_pixels'``
            a :class:`geopandas.GeoDataFrame` giving for each row 
            the columns "lat" and "lon" (with coordinates) 
            and a polygon giving the boundary of the pixel 
            given by lat/lon
        - ``'source_grid'``
            ``[da.lat,da.lon]`` of the grid used to create
            the pixel polygons
    

    Returns
    ---------------
    wm_out: dict
        A dictionary containing: 
        
        - ``'agg'``
            a dataframe containing all the fields of `gdf_in` (except
            geometry) and the additional columns: 

            - `coords`:  the lat/lon coordiates of all pixels that overlap
             the polygon of that row
            - `pix_idxs`: the linear indices of those pixels within the 
             `gdf_pixels` grid
            - `rel_area`: the relative area of each of the overlaps between
             the pixels and the polygon (summing to 1 - e.g. 
             if the polygon is exactly the size and location of
             two pixels, their rel_areas would be 0.5 each)
        - ``'source_grid'``: 
            a dictionary with keys 'lat' and 'lon' giving the 
            original lat/lon grid whose overlaps with the polygons
            was calculated
        - ``'geometry'``: 
            just the polygons from `gdf_in` 


    """
    
    # Add an index for each polygon as a column to make indexing easier
    #if 'poly_idx' not in gdf_in.columns:
    #    gdf_in['poly_idx'] = gdf_in.index.values
    gdf_in['poly_idx'] = np.arange(0,len(gdf_in))
        
    # Match up CRSes
    pix_agg['gdf_pixels'] = pix_agg['gdf_pixels'].to_crs(gdf_in.crs)

    # Get GeoDataFrame of the overlaps between every pixel and the polygons
    # (using the EASE grid https://nsidc.org/data/ease)
    if np.all(gdf_in.total_bounds[[1,3]]>0):
        # If min/max lat are both in NH, use North grid
        #epsg_set = {'init':'EPSG:6931'} (change to below bc of depreciation of {'init':...} format in geopandas)
        epsg_set = 'EPSG:6931'
    elif np.all(gdf_in.total_bounds[[1,3]]<0):
        # If min/max lat are both in SH, use South grid
        #epsg_set = {'init':'EPSG:6932'}
        epsg_set = 'EPSG:6932'
    else:
        # Otherwise, use the global/temperate grid
        #epsg_set = {'init':'EPSG:6933'}
        epsg_set = 'EPSG:6933'
    
    overlaps = gpd.overlay(gdf_in.to_crs(epsg_set),
                           pix_agg['gdf_pixels'].to_crs(epsg_set),
                           how='intersection')
    
    overlaps = overlaps.groupby('poly_idx').apply(find_rel_area)
    overlaps['lat'] = overlaps['lat'].astype(float)
    overlaps['lon'] = overlaps['lon'].astype(float)

    # Now, group by poly_idx (each polygon in the shapefile)
    ov_groups = overlaps.groupby('poly_idx')

    overlap_info = ov_groups.agg(list_or_first)

    overlap_info = overlap_info.rename(columns={'pix_idx': 'pix_idxs'})

    # Zip lat, lon columns into a list of (lat,lon) coordinates
    # (separate from above because as of 12/20, named aggs with 
    # multiple columns is still an open issue in the pandas github)
    overlap_info['coords'] = overlap_info.apply(lambda row: list(zip(row['lat'],row['lon'])),axis=1)
    overlap_info = overlap_info.drop(columns=['lat','lon'])

    # Reset index to make poly_idx a column for merging with gdf_in
    overlap_info = overlap_info.reset_index()

    # Merge in pixel overlaps to the input polygon geodataframe
    overlap_columns = ['pix_idxs', 'rel_area', 'coords', 'poly_idx']
    gdf_in = pd.merge(gdf_in, overlap_info[overlap_columns],'outer', on='poly_idx')

    # make the weight grid an xarray dataset for later dot product
    idx_cols = ['lat', 'lon', 'poly_idx']
    overlap_da = overlaps.set_index(idx_cols)['rel_area'].to_xarray()
    overlap_da = overlap_da.stack(loc=['lat', 'lon'])
    overlap_da = overlap_da.fillna(0)
    wm_out = weightmap(agg=gdf_in.drop('geometry', axis=1),
               source_grid=pix_agg['source_grid'],
               geometry=gdf_in.geometry,
               overlap_da = overlap_da)
    
    if 'weights' in pix_agg['gdf_pixels'].columns:
        wm_out.weights = pix_agg['gdf_pixels'].weights

    return wm_out


def aggregate(ds,wm):
    """ Aggregate raster variable(s) to polygon(s)
    
    Aggregates (N-D) raster variables in `ds` to the polygons
    in `gfd_out` - in other words, gives the weighted average
    of the values in [ds] based on each pixel's relative area
    overlap with the polygons. 
    
    The values will be additionally weighted if a `weight` was
    inputted into :func:`xagg.core.create_raster_polygons`
    
    The code checks whether the input lat/lon grid in `ds` is 
    equivalent to the linearly indexed grid in `wm`, or 
    if it can be cropped to that grid. 
    
    
    Parameters
    ---------------
    ds : :class:`xarray.Dataset`
        an :class:`xarray.Dataset` containing one or more
        variables with dimensions lat, lon (and possibly
        more). The dataset's geographic grid has to 
        include the lat/lon coordinates used in 
        determining the pixel overlaps in 
        :func:`xagg.core.get_pixel_overlaps` (and saved in 
        ``wm['source_grid']``)
               
    wm : :class:`xagg.classes.weightmap`
        the output to :func:`xagg.core.get_pixel_overlaps`; a 
        :class:`xagg.classes.weightmap` object containing 

        - ``['agg']``
            a dataframe, 
            with one row per polygon, and the columns `pix_idxs`
            and `rel_area`, giving the linear indices and 
            the relative area of each pixel over the polygon,
            respectively 
        - ``['source_grid']``  
            the lat/lon grid on which the aggregating parameters
            were calculated (and on which the linear indices 
            are based)
               
    Returns
    ---------------
    agg_out : :class:`xagg.classes.aggregated`
        an :class:`xagg.classes.aggregated` object with the aggregated variables 
    
    """
    # Turn into dataset if dataarray
    if type(ds)==xr.core.dataarray.DataArray:
      if ds.name is None:
        warnings.warn('An unnamed xr.DataArray was inputted instead of a xr.Dataset; the output variable will be "var"')
        ds = ds.to_dataset(name='var')
      else:
        ds = ds.to_dataset()


    # Run ds through fix_ds (to fix lat/lon names, lon coords)
    ds = fix_ds(ds)

    # Stack 
    ds = ds.stack(loc=('lat','lon'))
    
    # Adjust grid of [ds] if necessary to match 
    ds = subset_find(ds,wm.source_grid)
    
    # Set weights; or replace with ones if no additional weight information
    #if wm.weights != 'nowghts':
    if type(wm.weights) == pd.core.series.Series:
        weights = np.array([float(k) for k in wm.weights])
    else:
        if wm.weights != 'nowghts':
            warnings.warn('wm.weights is: \n '+print(wm.weights)+
                            ', \n which is not a supported weight vector (in a pandas series) '+
                            'or "nowghts" as a string. Assuming no weights are included...')
        weights = np.ones((len(wm.source_grid['lat'])))
    
    data_dict = dict()
    for var in ds.var():
        # Process for every variable that has locational information, but isn't a 
        # bound variable
        if ('bnds' not in ds[var].dims) & ('loc' in ds[var].dims):
            print('aggregating '+var+'...')
            var_array = ds[var]
            var_array = wm.overlap_da.dot(var_array)
            data_dict[var] = var_array

    ds_combined = xr.Dataset(data_dict)    
    df_combined = ds_combined.to_dataframe().reset_index()
    df_combined = df_combined.groupby('poly_idx').agg(list_or_first)

    wm.agg = pd.merge(wm.agg, df_combined, on='poly_idx')
    for var in ds.var():
        if ('bnds' not in ds[var].dims) & ('loc' in ds[var].dims):
            # convert to list of arrays - NOT SURE THIS IS THE RIGHT THING TO
            # DO, JUST TRYING TO MATCH ORIGINAL FORMAT
            wm.agg[var] = wm.agg[var].apply(np.array).apply(lambda x: [x])

    # Put in class format
    agg_out = aggregated(agg=wm.agg,source_grid=wm.source_grid,
    					 geometry=wm.geometry,ds_in=ds_combined,weights=wm.weights)

    # Return
    print('all variables aggregated to polygons!')
    return agg_out

