#!/usr/bin/env python3
#
# Tests the basic methods of the metropolis random walk MCMC routine.
#
# This file is part of PINTS.
#  Copyright (c) 2017-2018, University of Oxford.
#  For licensing information, see the LICENSE file distributed with the PINTS
#  software package.
#
import pints
import pints.toy as toy
import unittest
import numpy as np

debug = False


class TestMetropolisRandomWalkMCMC(unittest.TestCase):
    """
    Tests the basic methods of the metropolis random walk MCMC routine.
    """
    def __init__(self, name):
        super(TestMetropolisRandomWalkMCMC, self).__init__(name)

        # Create toy model
        self.model = toy.LogisticModel()
        self.real_parameters = [0.015, 500]
        self.times = np.linspace(0, 1000, 1000)
        self.values = self.model.simulate(self.real_parameters, self.times)

        # Add noise
        self.noise = 10
        self.values += np.random.normal(0, self.noise, self.values.shape)
        self.real_parameters.append(self.noise)
        self.real_parameters = np.array(self.real_parameters)

        # Create an object with links to the model and time series
        self.problem = pints.SingleSeriesProblem(
            self.model, self.times, self.values)

        # Create a uniform prior over both the parameters and the new noise
        # variable
        self.log_prior = pints.UniformLogPrior(
            [0.01, 400, self.noise * 0.1],
            [0.02, 600, self.noise * 100]
        )

        # Create a log likelihood
        self.log_likelihood = pints.UnknownNoiseLogLikelihood(self.problem)

        # Create an un-normalised log-posterior (log-likelihood + log-prior)
        self.log_posterior = pints.LogPosterior(
            self.log_likelihood, self.log_prior)

    def test_method(self):

        # Create mcmc
        x0 = self.real_parameters * 1.1
        mcmc = pints.MetropolisRandomWalkMCMC(x0)

        # Perform short run
        rate = []
        chain = []
        for i in range(100):
            x = mcmc.ask()
            fx = self.log_posterior(x)
            sample = mcmc.tell(fx)
            if i >= 50:
                chain.append(sample)
            rate.append(mcmc.acceptance_rate())
        chain = np.array(chain)
        rate = np.array(rate)
        self.assertEqual(chain.shape[0], 50)
        self.assertEqual(chain.shape[1], len(x0))
        self.assertEqual(rate.shape[0], 100)
        #TODO: Add more stringent tests

    def test_flow(self):

        # Test initial proposal is first point
        x0 = self.real_parameters
        mcmc = pints.MetropolisRandomWalkMCMC(x0)
        self.assertTrue(mcmc.ask() is mcmc._x0)

        # Double initialisation
        mcmc = pints.MetropolisRandomWalkMCMC(x0)
        mcmc.ask()
        self.assertRaises(RuntimeError, mcmc._initialise)

        # Tell without ask
        mcmc = pints.MetropolisRandomWalkMCMC(x0)
        self.assertRaises(RuntimeError, mcmc.tell, 0)

        # Repeated asks should return same point
        mcmc = pints.MetropolisRandomWalkMCMC(x0)
        # Get nearer accepting state
        for i in range(100):
            mcmc.tell(self.log_posterior(mcmc.ask()))
        x = mcmc.ask()
        for i in range(10):
            self.assertTrue(x is mcmc.ask())

        # Repeated tells should fail
        mcmc.tell(1)
        self.assertRaises(RuntimeError, mcmc.tell, 1)

        # Bad starting point
        mcmc = pints.MetropolisRandomWalkMCMC(x0)
        mcmc.ask()
        self.assertRaises(ValueError, mcmc.tell, float('-inf'))


if __name__ == '__main__':
    print('Add -v for more debug output')
    import sys
    if '-v' in sys.argv:
        debug = True
    unittest.main()