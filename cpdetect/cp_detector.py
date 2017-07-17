"""
Bayesian change point detection. Implementation of Ensign And Pande, J. Phys. Chem. B 2010, 114, 280-292 for
a normal and log-normal distribution

Author: Chaya D. Stern
"""

import numpy as np
import copy
from cpdetect.utils import logger
import time
import pandas as pd
import math
from scipy.special import gammaln
from collections import OrderedDict


class Detector(object):
    """
    Bayesian change point detection.

    :parameter
    observations: list of numpy arrays
        trajectories to find change point
    distribution: str
        distribution of underlying process (normal or log_normal)
    log_odds_threshold: int
        desired threshold. If log odds (log of Bayes factor) is greater than this threshold, segment will be split.
    """

    def __init__(self, observations, distribution, log_odds_threshold=0):
        """

        :param observations: list of numpy arrays
            list of observation trajectories
        :param distribution: str
            distribution of process (log_normal or normal)
        """
        self._observations = copy.deepcopy(observations)
        self._nobs = len(observations)
        self._Ts = [len(o) for o in observations]
        self.change_points = {}  # Dictionary containing change point time and its likelihood
        self.state_emission = {}  # Dictionary containing state's mean and sigma for segment
        self.loggamma = [-99, -99, -99]
        self.threshold = log_odds_threshold
        self.step_function = {}

        if distribution == 'log_normal':
            self._distribution = LogNormal()
            self.distribution = 'log_normal'
        elif distribution == 'normal' or distribution == 'gaussian':
            self._distribution = Normal()
            self.distribution = 'normal'
        else:
            raise ValueError('Use log_normal or normal distribution. I got something else')

        # Generate gamma table
        self._generate_loggamma_table()

    @property
    def nobservations(self):
        """ Number of observation trajectories """
        return self._nobs

    @property
    def observation_lengths(self):
        """ Return lengths of trajectories"""
        return self._Ts

    def _normal_lognormal_bf(self, obs):
        """
        Calculate Bayes factor P(D|H_2) / P(D|H_1) for normal or log-normal data

        :parameter:
        obs: np.array
            segment of trajectory to calculate Bayes factor
        :return:
        ts: int
             time point for split (argmax)
        log_odds: float

        """
        n = len(obs)
        if n < 6:
            logger().debug('Segment is less than 6 points')
            return None  # can't find a cp in data this small

        # Calculate mean and var
        mean, var = self._distribution.mean_var(obs)

        # the denominator. This is the easy part.
        denom = 1.5*np.log(np.pi) + (-n/2.0 + 0.5)*(np.log(n*var)) + self.loggamma[n]

        # BEGIN weight calculation
        # the numerator. A little trickier.
        weights = [0, 0, 0]  # the change cannot have occurred in the last 3 points

        for i in range(3, n-2):
            data_a = obs[0:i]
            n_a = len(data_a)
            data_b = obs[i:]
            n_b = len(data_b)

            mean_a, var_a = self._distribution.mean_var(data_a)
            mean_b, var_b = self._distribution.mean_var(data_b)

            mean_a2 = mean_a**2
            mean_b2 = mean_b**2

            wnumf1 = (-0.5*n_a + 0.5)*np.log(n_a) + (-0.5*n_a + 1)*np.log(var_a) + self.loggamma[n_a]
            wnumf2 = (-0.5*n_b + 0.5)*np.log(n_b) + (-0.5*n_b + 1)*np.log(var_b) + self.loggamma[n_b]

            wdenom = np.log(var_a + var_b) + np.log(mean_a2*mean_b2)

            weights.append((wnumf1 + wnumf2) - wdenom)

        weights.extend([0, 0])  # the change cannot have occurred at the last 2 points
        weights = np.array(weights)
        # END weight calculation
        num = 2.5*np.log(2.0) + np.log(abs(mean)) + weights.mean()
        log_odds = num - denom
        # Replace points where change cannot occur with negative infinity so that they cannot be argmax
        weights[0] = weights[1] = weights[2] = weights[-1] = weights[-2] = -np.inf
        logger().debug('    log num: ' + str(num))
        logger().debug('    denom: ' + str(denom))
        logger().debug('    log odds: ' + str(log_odds))

        # If there is a change point, then logodds will be greater than 0
        # Check for nan. This comes up if using log normal for a normal distribution.
        if math.isnan(log_odds):
            raise ValueError('Are you using the correct distribution?')
        if log_odds < self.threshold:

            logger().debug('    Log Odds: ' + str(log_odds) + ' is less than threshold ' + str(self.threshold) +
                          '. No change point found')
            return None
        return weights.argmax(), log_odds

    def _generate_loggamma_table(self):
        """
        calculate log gamma for all N
        """
        for i in range(3, max(self._Ts) + 1):
            self.loggamma.append(gammaln(0.5*i - 1))

    def detect_cp(self):
        """
        Bayesian detection of Intensity changes. This function detects the changes, their timepoints and then
        finds the state emission for each segment to draw the step function
        """

        logger().info('=======================================')
        logger().info('Running change point detector')
        logger().info('=======================================')
        logger().info('   input observations: '+str(self.nobservations)+ ' of length ' + str(self.observation_lengths))

        initial_time = time.time()

        for k in range(self._nobs):
            logger().info('Running cp detector on traj ' + str(k))
            logger().info('---------------------------------')
            self.change_points['traj_%s' %str(k)] = pd.DataFrame(columns=['ts', 'log_odds', 'start_end'])
            self.change_points['traj_%s' %str(k)]['ts'] = self.change_points['traj_%s' %str(k)]['ts'].astype(int)
            obs = self._observations[k]
            self._split(obs, 0, self.observation_lengths[k], k)
            logger().info('Generating step fucntion')
            logger().info('---------------------------------')
            self._generate_step_function(obs, k)

        final_time = time.time()

        logger().info('Elapsed time: ' + str(final_time-initial_time))

    def _split(self, obs, start, end,  itraj):
        """
        This function takes an array of observations and checks if it should be split

        :param obs: np.array
            trajectory to check for change point
        :param start: int
            start of segment to check for change point
        :param end: int
            end of segment
        :param itraj: int
            index of trajectory
        """
        # recursive function to find all ts and logg odds
        logger().debug('    Trying to split segment start at ' + str(start) + ' end ' + str(end))
        result = self._normal_lognormal_bf(obs[start:end])

        if result is None:
            logger().debug("      Can't split segment start at " + str(start) + " end at " + str(end))
            return
        else:
            log_odds = result[-1]
            ts = start + result[0]
            self.change_points['traj_%s' % str(itraj)] = self.change_points['traj_%s' % str(itraj)].append(
                    {'ts': ts, 'log_odds': log_odds, 'start_end': (start, end)}, ignore_index=True)
            logger().info('    Found a new change point at: ' + str(ts) + '!!')
            self._split(obs, start, ts, itraj)
            self._split(obs, ts, end, itraj)

    def _generate_step_function(self, obs, itraj):
        """Draw step function based on sample mean

        :parameter obs
            trajectory
        :parameter itraj: int
            index of trajectory
        """

        self.state_emission['traj_%s' % str(itraj)] = pd.DataFrame(columns=['partition', 'sample_mu', 'sample_sigma'])

        # First sort ts of traj
        ts = self.change_points['traj_%s' % str(itraj)]['ts'].values
        if len(ts) == 0:
            logger().info('No change point was found')
            self.step_function['traj_%s' % str(itraj)] = np.ones(self.observation_lengths[itraj]-1)
            mean, var = self._distribution.mean_var(obs)
            self.step_function['traj_%s' % str(itraj)] = self.step_function['traj_%s' % str(itraj)]*np.exp(mean)
            return
        ts.sort()
        # populate data frame with partitions, sample mean and sigma
        partitions = [(0, int(ts[0]))]
        mean, var = self._distribution.mean_var(obs[0:ts[0]])
        means = [mean]
        sigmas = [var]
        for i, j in enumerate(ts):
            try:
                partitions.append((int(j+1), int(ts[i+1])))
                mean, var = self._distribution.mean_var(obs[j+1:ts[i+1]])
                means.append(mean)
                sigmas.append(var)

            except IndexError:
                partitions.append((int(ts[-1]+1), int(self.observation_lengths[itraj]-1)))
                mean, var = self._distribution.mean_var(obs[ts[-1]+1:len(obs)-1])
                means.append(mean)
                sigmas.append(var)
        self.state_emission['traj_%s' % str(itraj)]['partition'] = partitions
        self.state_emission['traj_%s' % str(itraj)]['sample_mu'] = means
        self.state_emission['traj_%s' % str(itraj)]['sample_sigma'] = sigmas

        # generate step function
        self.step_function['traj_%s' % str(itraj)] = np.ones(self.observation_lengths[itraj])
        for index, row in self.state_emission['traj_%s' % str(itraj)].iterrows():
            self.step_function['traj_%s' % str(itraj)][row['partition'][0]:row['partition'][1]+1] = \
                np.exp(row['sample_mu'])

    def to_csv(self, filename=None):
        """
        export change_points data frame to csv file
        :parameter:
            filename: str

        :return:
            csv if no filename given. Otherwise, saves csv file
        """
        frames = []
        keys = []
        for i in self.change_points:
            keys.append(i)
            frames.append(self.change_points[i])
        all_f = pd.concat(frames, keys=keys)

        if filename:
            all_f.to_csv(filename)
        else:
            return all.to_csv()

class LogNormal(object):

    @classmethod
    def mean_var(cls, data):
        """
        calculate log normal mean and variance (loc and scale)
        :parameter:
        data: np.array
            data points to calculate mean and var

        :return: (float, float)
            loc, scale of data
        """
        n = len(data)
        logx = np.log(data)
        loc = logx.sum()/n
        scale = ((logx - loc)**2).sum()/n
        return loc, scale


class Normal(object):

    @classmethod
    def mean_var(cls, data):
        return data.mean(), data.var()

