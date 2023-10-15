import os
import math
import numpy as np
import h5py
import warnings
from packaging.version import parse as parse_version
from packaging.specifiers import SpecifierSet

# the latest version pywavesurfer was tested against
_latest_version = 0.982
_over_version_1 = SpecifierSet(">=1.0")


# from pywavesurfer.ws import * will only import loadDataFile
__all__ = ['loadDataFile']


def loadDataFile(filename, format_string='double'):
    """ Return the PyWaveSurfer data as a dictionary. This
    convenience function returns the entire loaded dataset.
    Used for backwards compatability with versions prior
    to introduction of lazy loading.
    """
    with PyWaveSurferData(filename, format_string) as wavesurfer:
        data_file_as_dict = wavesurfer.load_all_data()
    return data_file_as_dict


class PyWaveSurferData:

    def __init__(self, filename, format_string="double"):
        """ Loads Wavesurfer data file.

        :param filename: File to load (has to be with .h5 extension)
        :param format_string: optional: the return type of the data, defaults to double.
                              Could be 'single', or 'raw'.
        :return: dictionary with a structure array with one element per sweep in the
                 data file.
        """
        self.format_string = format_string

        # Check that file exists
        if not os.path.isfile(filename):
            raise IOError("The file %s does not exist." % filename)

        # Check that file has proper extension
        (_, ext) = os.path.splitext(filename)
        if ext != ".h5":
            raise RuntimeError("File must be a WaveSurfer-generated HDF5 (.h5) file.")

        self.file = h5py.File(filename, mode='r')

        self.data_file_as_dict = self.get_metadata_dict()

        self.analog_channel_scales, self.analog_scaling_coefficients, self.n_a_i_channels = self.get_scaling_coefficients()

    def close_file(self):
        if not self.file.closed():
            self.file.close()

    def __enter__(self):
        """ This and `__exit__` ensure the  class can be
        used in a `with` statement.
        """
        return self

    def __exit__(self):
        self.close_file()

    # ----------------------------------------------------------------------------------
    # Fill Metadata Dict
    # ----------------------------------------------------------------------------------

    def get_metadata_dict(self):
        data_file_as_dict = self.recursive_crawl_h5_group(self.file)
        data_file_as_dict = self.fix_sampling_rate_for_older_versions(data_file_as_dict)

        return data_file_as_dict

    def recursive_crawl_h5_group(self, group):
        """ Recursively store the header information from the .h5 file
        into a dictionary.

        The entry 'analogScans' hold the data from the 'sweep_xxxx'
        keys, whereas in older version these were stored directly
        in 'trial_xxxx' keys. For lazy loading, the raw data in 'sweep'
        or 'trial' keys is is not loaded at this stage.
        """
        result = dict()

        item_names = list(group.keys())
        for item_name in item_names:
            item = group[item_name]
            if isinstance(item, h5py.Group):
                field_name = self.field_name_from_hdf_name(item_name)
                result[field_name] = self.recursive_crawl_h5_group(item)
            elif isinstance(item, h5py.Dataset):
                field_name = self.field_name_from_hdf_name(item_name)
                if item_name != "analogScans" and item_name[0:5] != "trial":
                    result[field_name] = item[()]
            else:
                pass

        return result

    def field_name_from_hdf_name(self, hdf_name):
        """ Convert the name of an HDF dataset/group to something that is a legal
        Matlab struct field name.  We do this even in Python, just to be consistent.
        """
        try:
            # the group/dataset name seems to be a number.  If it's an integer, we can deal, so check that.
            hdf_name_as_double = float(hdf_name)
            if hdf_name_as_double == round(hdf_name_as_double):
                # If get here, group name is an integer, so we prepend with an "n" to get a valid field name
                field_name = "n{:%s}".format(hdf_name)
            else:
                # Not an integer.  Give up.
                raise RuntimeError("Unable to convert group/dataset name {:%s} to a valid field name.".format(hdf_name))
        except ValueError:
            # This is actually a good thing, b/c it means the groupName is not
            # simply a number, which would be an illegal field name
            field_name = hdf_name

        return field_name

    def fix_sampling_rate_for_older_versions(self, data_file_as_dict):
        """ Correct the samples rates for files that were generated by versions
        of WS which didn't coerce the sampling rate to an allowed rate.
        """
        header = data_file_as_dict["header"]
        if "VersionString" in header:
            version_numpy = header["VersionString"]  # this is a scalar numpy array with a weird datatype
            version = version_numpy.tobytes().decode("utf-8")
            parsed_version = parse_version(version)
            if parsed_version in _over_version_1:
                if parsed_version > parse_version(str(_latest_version)):
                    warnings.warn('You are reading a WaveSurfer file version this module was not tested with: '
                                  'file version %s, latest version tested: %s'
                                  % (parsed_version.public, parse_version(str(_latest_version)).public), RuntimeWarning)
            elif float(version) > _latest_version:
                warnings.warn('You are reading a WaveSurfer file version this module was not tested with: '
                              'file version %s, latest version tested: %s' % (version, _latest_version), RuntimeWarning)
        else:
            # If no VersionsString field, the file is from an old old version
            parsed_version = parse_version('0.0')

        # version 0.912 has the problem, version 0.913 does not
        if parsed_version not in _over_version_1 and parsed_version.release is not None:
            version_string = str(parsed_version.release[1])
            ver_len = len(version_string)
            if int(version_string[0]) < 9 or (ver_len >= 2 and int(version_string[1]) < 1) or \
               (ver_len >= 3 and int(version_string[1]) <= 1 and int(version_string[2]) <= 2):
                # Fix the acquisition sample rate, if needed
                nominal_acquisition_sample_rate = float(header["Acquisition"]["SampleRate"])
                nominal_n_timebase_ticks_per_sample = 100.0e6 / nominal_acquisition_sample_rate
                if nominal_n_timebase_ticks_per_sample != round(
                        nominal_n_timebase_ticks_per_sample):  # should use the python round, not numpy round
                    actual_acquisition_sample_rate = 100.0e6 / math.floor(
                        nominal_n_timebase_ticks_per_sample)  # sic: the boards floor() for acq, but round() for stim
                    header["Acquisition"]["SampleRate"] = np.array(actual_acquisition_sample_rate)
                    data_file_as_dict["header"] = header
                # Fix the stimulation sample rate, if needed
                nominal_stimulation_sample_rate = float(header["Stimulation"]["SampleRate"])
                nominal_n_timebase_ticks_per_sample = 100.0e6 / nominal_stimulation_sample_rate
                if nominal_n_timebase_ticks_per_sample != round(nominal_n_timebase_ticks_per_sample):
                    actual_stimulation_sample_rate = 100.0e6 / round(
                        nominal_n_timebase_ticks_per_sample)  # sic: the boards floor() for acq, but round() for stim
                    header["Stimulation"]["SampleRate"] = np.array(actual_stimulation_sample_rate)
                    data_file_as_dict["header"] = header

        return data_file_as_dict

    # ----------------------------------------------------------------------------------
    # Get gain and scaling coefficients
    # ----------------------------------------------------------------------------------

    def get_scaling_coefficients(self):
        """ Get the correct scale and gain coefficients based on
        the file version.
        """
        header = self.data_file_as_dict["header"]

        if "NAIChannels" in header:
            n_a_i_channels = header["NAIChannels"]
        else:
            acq = header["Acquisition"]
            if "AnalogChannelScales" in acq:
                all_analog_channel_scales = acq["AnalogChannelScales"]
            else:
                # This is presumably a very old file, from before we supported
                # digital inputs
                all_analog_channel_scales = acq["ChannelScales"]
            n_a_i_channels = all_analog_channel_scales.size  # element count

        if self.format_string.lower() != "raw" and n_a_i_channels > 0:
            try:
                if "AIChannelScales" in header:
                    # Newer files have this field, and lack
                    # header.Acquisition.AnalogChannelScales
                    all_analog_channel_scales = header["AIChannelScales"]
                else:
                    # Fallback for older files
                    all_analog_channel_scales = header["Acquisition"][
                        "AnalogChannelScales"]
            except KeyError:
                raise KeyError("Unable to read channel scale information from file.")
            try:
                if "IsAIChannelActive" in header:
                    # Newer files have this field, and lack
                    # header.Acquisition.AnalogChannelScales
                    is_active = header["IsAIChannelActive"].astype(bool)
                else:
                    # Fallback for older files
                    is_active = header["Acquisition"]["IsAnalogChannelActive"].astype(
                        bool)
            except KeyError:
                raise KeyError(
                    "Unable to read active/inactive channel information from file.")
            analog_channel_scales = all_analog_channel_scales[is_active]  # TODO

            # read the scaling coefficients
            try:
                if "AIScalingCoefficients" in header:
                    analog_scaling_coefficients = header[
                        "AIScalingCoefficients"]  # TODO
                else:
                    analog_scaling_coefficients = header["Acquisition"][
                        "AnalogScalingCoefficients"]
            except KeyError:
                raise KeyError("Unable to read channel scaling coefficients from file.")

        else:
            analog_channel_scales = analog_scaling_coefficients = None

        return analog_channel_scales, analog_scaling_coefficients, n_a_i_channels

    # ----------------------------------------------------------------------------------
    # Data Getters
    # ----------------------------------------------------------------------------------

    def get_traces(self, segment_index, start_frame, end_frame, return_scaled=True):
        """
        Get traces for a segment (i.e. a specific 'sweep' or 'trial'
        number) indexied between `start_frame` and `end_frame`.

        If `return_scaled` is `True`, data will be scaled according to
        the `format_string` argument passed during class construction.
        """
        ordered_sweep_names = self.get_ordered_sweep_names()
        sweep_name = ordered_field_names[segment_index]

        # Index out the data and scale if required.
        if sweep_name[0:5] == "sweep":
            analog_data_as_counts = self.file[sweep_name]["analogScans"][:, start_frame:end_frame]
        else:
            analog_data_as_counts = self.file[sweep_name][:, start_frame:end_frame]

        if return_scaled and self.format_string.lower() == "raw":
            raise ValueError("`return_scaled` cannot be `True` is `format_string` is 'raw'.")

        if return_scaled and self.n_a_i_channels > 0:
            if self.format_string.lower() == "single":
                traces = scaled_single_analog_data_from_raw(
                    analog_data_as_counts,
                    self.analog_channel_scales,
                    self.analog_scaling_coefficients)
            else:
                traces = scaled_double_analog_data_from_raw(
                    analog_data_as_counts,
                    self.analog_channel_scales,
                    self.analog_scaling_coefficients)
        else:
            traces = analog_data_as_counts

        return traces

    def get_ordered_sweep_names(self):
        """ Take the data field names (e.g. sweep_0001, sweep_0002), ensure they
        are in the correct order and index according to `segment_index`.
        """
        field_names = [name for name in self.file if name[0:5] in ["sweep", "trial"]]
        sweep_nums = [int(ele[6:]) for ele in field_names]
        ordered_field_names = [field_names[num - 1] for num in sweep_nums]

        return ordered_field_names

    def load_all_data(self):
        """
        A convenience function to load into the `data_file_as_dict`
        all data in the file.
        """
        idx = 0
        for field_name in self.file:

            if len(field_name) >= 5 and (field_name[0:5] == "sweep" or field_name[0:5] == "trial"):

                if field_name[0:5] == "sweep":
                    num_samples = self.file[field_name]["analogScans"].size
                else:
                    num_samples = self.file[field_name].size

                scaled_analog_data = self.get_traces(segment_index=idx, start_frame=0, end_frame=num_samples)

                if field_name[0:5] == "sweep":
                    self.data_file_as_dict[field_name]["analogScans"] = scaled_analog_data
                else:
                    self.data_file_as_dict[field_name] = scaled_analog_data

        return self.data_file_as_dict

# ----------------------------------------------------------------------------------
# Scaling Functions
# ----------------------------------------------------------------------------------

def scaled_double_analog_data_from_raw(data_as_ADC_counts, channel_scales, scaling_coefficients):
    """
    Function to convert raw ADC data as int16s to doubles, taking to the
    per-channel scaling factors into account.

      data_as_ADC_counts: n_channels x nScans int16 array
      channel_scales: double vector of length n_channels, each element having
                      (implicit) units of V/(native unit), where each
                      channel has its own native unit.
      scaling_coefficients: n_channels x nCoefficients  double array,
                           contains scaling coefficients for converting
                           ADC counts to volts at the ADC input.

      scaled_data: nScans x n_channels double array containing the scaled
                  data, each channel with it's own native unit.
    """
    inverse_channel_scales = 1.0 / channel_scales  # if some channel scales are zero, this will lead to nans and/or infs
    n_channels = channel_scales.size
    scaled_data = np.empty(data_as_ADC_counts.shape)
    for i in range(0, n_channels):
        scaled_data[i, :] = inverse_channel_scales[i] * np.polyval(np.flipud(scaling_coefficients[i, :]),
                                                                   data_as_ADC_counts[i, :])
    return scaled_data


def scaled_single_analog_data_from_raw(data_as_ADC_counts, channel_scales, scaling_coefficients):
    scaled_data = scaled_double_analog_data_from_raw(data_as_ADC_counts, channel_scales, scaling_coefficients)
    return scaled_data.astype('single')
