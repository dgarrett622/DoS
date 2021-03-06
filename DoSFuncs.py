# -*- coding: utf-8 -*-
"""
Created on Wed Feb 1, 2017
Updated Mon Oct 22, 2018

@author: dg622@cornell.edu
"""

import numpy as np
import os
import EXOSIMS.MissionSim as MissionSim
import sympy
from sympy.solvers import solve
import scipy.integrate as integrate
import scipy.interpolate as interpolate
import scipy.optimize as optimize
import astropy.constants as const
import astropy.units as u
try:
    import cPickle as pickle
except:
    import pickle
from ortools.linear_solver import pywraplp
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

class DoSFuncs(object):
    '''Calculates depth of search values for a given input EXOSIMS json script. 
    Occurrence rates are determined from the EXOSIMS PlanetPopulation specified.
    
    'core_contrast' must be specified in the input json script as either a 
    path to a fits file or a constant value, otherwise the default contrast 
    value from EXOSIMS will be used
    
    path must be specified
    
    Args:
        path (str):
            path to json script for EXOSIMS
        abins (int):
            number of semi-major axis bins for depth of search grid (optional)
        Rbins (int):
            number of planetary radius bins for depth of search grid (optional)
        maxTime (float):
            maximum total integration time in days (optional)
        intCutoff (float):
            integration cutoff time per target in days (optional)
        dMag (float):
            limiting dMag value for integration time calculation (optional)
        WA_targ (astropy Quantity):
            working angle for target astrophysical contrast (optional)
            
    Attributes:
        result (dict):
            dictionary containing results of the depth of search calculations
            Keys include:
                NumObs (dict):
                    dictionary containing number of observations, key is: 'all' 
                aedges (ndarray):
                    1D array of semi-major axis bin edges in AU
                Redges (ndarray):
                    1D array of planetary radius bin edges in R_earth
                DoS (dict):
                    dictionary containing 2D array of depth of search key is: 'all'
                occ_rates (dict):
                    dictionary containing 2D array of occurrence rates determined
                    from EXOSIMS PlanetPopulation, key is: 'all'
                DoS_occ (dict):
                    dictionary containing 2D array of depth of search convolved
                    with the extrapolated occurrence rates, keys is: 'all',
        sim (object):
            EXOSIMS.MissionSim object used to generate target list and 
            integration times
        outspec (dict):
            EXOSIMS.MissionSim output specification
    
    '''
    
    def __init__(self, path=None, abins=100, Rbins=30, maxTime=365.0, intCutoff=30.0, dMag=None, WA_targ=None):
        if path is None:
            raise ValueError('path must be specified')
        if path is not None:
            # generate EXOSIMS.MissionSim object to calculate integration times
            self.sim = MissionSim.MissionSim(scriptfile=path)
            print 'Acquired EXOSIMS data from %r' % (path)
        if dMag is not None:
            try:
                float(dMag)
            except TypeError:
                print 'dMag can have only one value'
        if WA_targ is not None:
            try:
                float(WA_targ.value)
            except AttributeError:
                print 'WA_targ must be astropy Quantity'
            except TypeError:
                print 'WA_targ can have only one value'
        self.result = {}
        # minimum and maximum values of semi-major axis and planetary radius
        # NO astropy Quantities
        amin = self.sim.PlanetPopulation.arange[0].to('AU').value
        amax = self.sim.PlanetPopulation.arange[1].to('AU').value
        Rmin = self.sim.PlanetPopulation.Rprange[0].to('earthRad').value
        assert Rmin < 45.0, 'Minimum planetary radius is above extrapolation range'
        if Rmin < 0.35:
            print 'Rmin reset to 0.35*R_earth'
            Rmin = 0.35
        Rmax = self.sim.PlanetPopulation.Rprange[1].to('earthRad').value
        assert Rmax > 0.35, 'Maximum planetary radius is below extrapolation range'
        if Rmax > 45.0:
            print 'Rmax reset to 45.0*R_earth'
        assert Rmax > Rmin, 'Maximum planetary radius is less than minimum planetary radius'
        # need to get Cmin from contrast curve
        mode = filter(lambda mode: mode['detectionMode'] == True, self.sim.OpticalSystem.observingModes)[0]
        WA = np.linspace(mode['IWA'], mode['OWA'], 50)
        syst = mode['syst']
        lam = mode['lam']
        if dMag is None:
            # use dMagLim when dMag not specified
            dMag = self.sim.Completeness.dMagLim
        fZ = self.sim.ZodiacalLight.fZ0
        fEZ = self.sim.ZodiacalLight.fEZ0
        if WA_targ is None:
            core_contrast = syst['core_contrast'](lam,WA)
            contrast = interpolate.interp1d(WA.to('arcsec').value, core_contrast, \
                                    kind='cubic', fill_value=1.0)
            # find minimum value of contrast
            opt = optimize.minimize_scalar(contrast, \
                                       bounds=[mode['IWA'].to('arcsec').value, \
                                               mode['OWA'].to('arcsec').value],\
                                               method='bounded')
            Cmin = opt.fun
            WA_targ = opt.x*u.arcsec
        
        t_int1 = self.sim.OpticalSystem.calc_intTime(self.sim.TargetList,np.array([0]),fZ,fEZ,dMag,WA_targ,mode)
        t_int1 = np.repeat(t_int1.value,len(WA))*t_int1.unit
        sInds = np.repeat(0,len(WA))
        fZ1 = np.repeat(fZ.value,len(WA))*fZ.unit
        fEZ1 = np.repeat(fEZ.value,len(WA))*fEZ.unit
        core_contrast = 10.0**(-0.4*self.sim.OpticalSystem.calc_dMag_per_intTime(t_int1,self.sim.TargetList,sInds,fZ1,fEZ1,WA,mode))
        contrast = interpolate.interp1d(WA.to('arcsec').value,core_contrast,kind='cubic',fill_value=1.0)
        opt = optimize.minimize_scalar(contrast,bounds=[mode['IWA'].to('arcsec').value,mode['OWA'].to('arcsec').value],method='bounded')
        Cmin = opt.fun
        
        # find expected values of p and R
        if self.sim.PlanetPopulation.prange[0] != self.sim.PlanetPopulation.prange[1]:
            if hasattr(self.sim.PlanetPopulation,'ps'):
                f = lambda R: self.sim.PlanetPopulation.get_p_from_Rp(R*u.earthRad)*self.sim.PlanetPopulation.dist_radius(R)
                pexp, err = integrate.quad(f,self.sim.PlanetPopulation.Rprange[0].value,\
                                           self.sim.PlanetPopulation.Rprange[1].value,\
                                           epsabs=0,epsrel=1e-6,limit=100)
            else:
                f = lambda p: p*self.sim.PlanetPopulation.dist_albedo(p)
                pexp, err = integrate.quad(f,self.sim.PlanetPopulation.prange[0],\
                                       self.sim.PlanetPopulation.prange[1],\
                                        epsabs=0,epsrel=1e-6,limit=100)
        else:
            pexp = self.sim.PlanetPopulation.prange[0]
        print 'Expected value of geometric albedo: %r' % (pexp)
        if self.sim.PlanetPopulation.Rprange[0] != self.sim.PlanetPopulation.Rprange[1]:
            f = lambda R: R*self.sim.PlanetPopulation.dist_radius(R)
            Rexp, err = integrate.quad(f,self.sim.PlanetPopulation.Rprange[0].to('earthRad').value,\
                                       self.sim.PlanetPopulation.Rprange[1].to('earthRad').value,\
                                        epsabs=0,epsrel=1e-4,limit=100)
            Rexp *= u.earthRad.to('AU')
        else:
            Rexp = self.sim.PlanetPopulation.Rprange[0].to('AU').value
        
        # minimum and maximum separations
        smin = (np.tan(mode['IWA'])*self.sim.TargetList.dist).to('AU').value
        smax = (np.tan(mode['OWA'])*self.sim.TargetList.dist).to('AU').value
        smax[smax>amax] = amax
    
        # include only stars where smin > amin
        bigger = np.where(smin>amin)[0]
        self.sim.TargetList.revise_lists(bigger)
        smin = smin[bigger]
        smax = smax[bigger]
    
        # include only stars where smin < amax
        smaller = np.where(smin<amax)[0]
        self.sim.TargetList.revise_lists(smaller)
        smin = smin[smaller]
        smax = smax[smaller]
        
        # calculate integration times
        sInds = np.arange(self.sim.TargetList.nStars)
        
        # calculate maximum integration time
        t_int = self.sim.OpticalSystem.calc_intTime(self.sim.TargetList, sInds, fZ, fEZ, dMag, WA_targ, mode)
        
        # remove integration times above cutoff
        cutoff = np.where(t_int.to('day').value<intCutoff)[0]
        self.sim.TargetList.revise_lists(cutoff)
        smin = smin[cutoff]
        smax = smax[cutoff]
        t_int = t_int[cutoff]

        print 'Beginning ck calculations'
        ck = self.find_ck(amin,amax,smin,smax,Cmin,pexp,Rexp)
        # offset to account for zero ck values with nonzero completeness
        ck += ck[ck>0.0].min()*1e-2
        print 'Finished ck calculations'
        
        print 'Beginning ortools calculations to determine list of observed stars'
        sInds = self.select_obs(t_int.to('day').value,maxTime,ck)
        print 'Finished ortools calculations'
        # include only stars chosen for observation
        self.sim.TargetList.revise_lists(sInds)
        smin = smin[sInds]
        smax = smax[sInds]
        t_int = t_int[sInds]
        ck = ck[sInds]
        
        # get contrast array for given integration times
        sInds2 = np.arange(self.sim.TargetList.nStars)
        fZ2 = np.repeat(fZ.value,len(WA))*fZ.unit
        fEZ2 = np.repeat(fEZ.value,len(WA))*fEZ.unit
        C_inst = np.zeros((len(sInds2),len(WA)))
        for i in xrange(len(sInds2)):
            t_int2 = np.repeat(t_int[i].value,len(WA))*t_int.unit
            sInds2a = np.repeat(sInds2[i],len(WA))
            C_inst[i,:] = 10.0**(-0.4*self.sim.OpticalSystem.calc_dMag_per_intTime(t_int2,self.sim.TargetList,sInds2a,fZ2,fEZ2,WA,mode))
        
        # store number of observed stars in result
        self.result['NumObs'] = {"all": self.sim.TargetList.nStars}
        print 'Number of observed targets: %r' % self.sim.TargetList.nStars

        # find bin edges for semi-major axis and planetary radius in AU
        aedges = np.logspace(np.log10(amin), np.log10(amax), abins+1)
        Redges = np.logspace(np.log10(Rmin*u.earthRad.to('AU')), \
                         np.log10(Rmax*u.earthRad.to('AU')), Rbins+1)
        # store aedges and Redges in result
        self.result['aedges'] = aedges
        self.result['Redges'] = Redges/u.earthRad.to('AU')
    
        aa, RR = np.meshgrid(aedges,Redges) # in AU
    
        # get depth of search 
        print 'Beginning depth of search calculations for observed stars'
        if self.sim.TargetList.nStars > 0:
            DoS = self.DoS_sum(aedges, aa, Redges, RR, pexp, smin, smax, \
                           self.sim.TargetList.dist.to('pc').value, C_inst, WA.to('arcsecond').value)
        else:
            DoS = np.zeros((aa.shape[0]-1,aa.shape[1]-1))
        print 'Finished depth of search calculations'
        # store DoS in result
        self.result['DoS'] = {"all": DoS}
        
        # find occurrence rate grid
        Redges /= u.earthRad.to('AU')
        etas = np.zeros((len(Redges)-1,len(aedges)-1))
        # get joint pdf of semi-major axis and radius
        if hasattr(self.sim.PlanetPopulation,'dist_sma_radius'):
            func = lambda a,R: self.sim.PlanetPopulation.dist_sma_radius(a,R)
        else:
            func = lambda a,R: self.sim.PlanetPopulation.dist_sma(a)*self.sim.PlanetPopulation.dist_radius(R)
        aa, RR = np.meshgrid(aedges,Redges)
        r_norm = Redges[1:] - Redges[:-1]
        a_norm = aedges[1:] - aedges[:-1]
        norma, normR = np.meshgrid(a_norm,r_norm)
        tmp = func(aa,RR)
        etas = 0.25*(tmp[:-1,:-1]+tmp[1:,:-1]+tmp[:-1,1:]+tmp[1:,1:])*norma*normR
#        for i in xrange(len(Redges)-1):
#            print('{} out of {}'.format(i+1,len(Redges)-1))
#            for j in xrange(len(aedges)-1):
#                etas[i,j] = integrate.dblquad(func,Redges[i],Redges[i+1],lambda x: aedges[j],lambda x: aedges[j+1])[0]
        etas *= self.sim.PlanetPopulation.eta
        self.result['occ_rates'] = {"all": etas}
        
        # Multiply depth of search with occurrence rates
        
        print 'Multiplying depth of search grid with occurrence rate grid'
        DoS_occ = DoS*etas*norma*normR
        self.result['DoS_occ'] = {"all": DoS_occ}
        
        # store MissionSim output specification dictionary
        self.outspec = self.sim.genOutSpec()
        print 'Calculations finished'
    
    def one_DoS_grid(self,a,R,p,smin,smax,Cmin):
        '''Calculates completeness for one star on constant semi-major axis--
        planetary radius grid
    
        Args:
            a (ndarray):
                2D array of semi-major axis values in AU
            R (ndarray):
                2D array of planetary radius values in AU
            p (float):
                average geometric albedo value
            smin (float):
                minimum separation in AU
            smax (float):
                maximum separation in AU
            Cmin (ndarray):
                2D array of minimum contrast
    
        Returns:
            f (ndarray):
                2D array of depth of search values for one star on 2D grid
    
        '''
        
        a = np.array(a, ndmin=1, copy=False)
        R = np.array(R, ndmin=1, copy=False)
        Cmin = np.array(Cmin, ndmin=1, copy=False)

        f = np.zeros(a.shape)
        # work on smax < a first
        fg = f[smax<a]
        ag = a[smax<a]
        Rg = R[smax<a]
        Cgmin = Cmin[smax<a]

        b1g = np.arcsin(smin/ag)
        b2g = np.pi-np.arcsin(smin/ag)
        b3g = np.arcsin(smax/ag)
        b4g = np.pi-np.arcsin(smax/ag)
        
        C1g = (p*(Rg/ag)**2*np.cos(b1g/2.0)**4)
        C2g = (p*(Rg/ag)**2*np.cos(b2g/2.0)**4)
        C3g = (p*(Rg/ag)**2*np.cos(b3g/2.0)**4)
        C4g = (p*(Rg/ag)**2*np.cos(b4g/2.0)**4)
        
        C2g[C2g<Cgmin] = Cgmin[C2g<Cgmin]
        C3g[C3g<Cgmin] = Cgmin[C3g<Cgmin]
        
        vals = C3g > C1g
        C3g[vals] = 0.0
        C1g[vals] = 0.0
        vals = C2g > C4g
        C2g[vals] = 0.0
        C4g[vals] = 0.0
        
        fg = (ag/np.sqrt(p*Rg**2)*(np.sqrt(C4g)-np.sqrt(C2g)+np.sqrt(C1g)-np.sqrt(C3g)))
        
        fl = f[smax>=a]
        al = a[smax>=a]
        Rl = R[smax>=a]
        Clmin = Cmin[smax>=a]
        
        b1l = np.zeros(al.shape)
        b1l[smin/al < 1.0] = np.arcsin(smin/al[smin/al < 1.0])
        b2l = np.pi*np.ones(al.shape)
        b2l[smin/al < 1.0] = np.pi-np.arcsin(smin/al[smin/al < 1.0])
        
        C1l = np.ones(al.shape)
        C1l[smin/al < 1.0] = p*(Rl[smin/al < 1.0]/al[smin/al < 1.0])**2*np.cos(b1l[smin/al < 1.0]/2.0)**4
        C2l = np.ones(al.shape)
        C2l[smin/al < 1.0] = p*(Rl[smin/al < 1.0]/al[smin/al < 1.0])**2*np.cos(b2l[smin/al < 1.0]/2.0)**4

        C2l[C2l<Clmin] = Clmin[C2l<Clmin]
        vals = C2l > C1l

        C1l[vals] = 0.0
        C2l[vals] = 0.0

        fl = (al/np.sqrt(p*Rl**2)*(np.sqrt(C1l)-np.sqrt(C2l)))

        f[smax<a] = fg
        f[smax>=a] = fl
        f[smin>a] = 0.0

        return f
    
    def one_DoS_bins(self,a,R,p,smin,smax,Cmin):
        '''Calculates depth of search for each bin by integrating the
        completeness for given semi-major axis and planetary radius
        
        Args:
            a (ndarray):
                2D grid of semi-major axis bin edges in AU
            R (ndarray):
                2D grid of planetary radius bin edges in R_Earth
            p (float):
                expected value of geometric albedo
            smin (float):
                minimum separation in AU
            smax (float):
                maximum separation in AU
            Cmin (ndarray):
                2D grid of minimum contrast
        
        Returns:
            f (ndarray):
                2D array of depth of search values in each bin
        
        '''
        
        tmp = self.one_DoS_grid(a,R,p,smin,smax,Cmin)
        f = 0.25*(tmp[:-1,:-1]+tmp[1:,:-1]+tmp[:-1,1:]+tmp[1:,1:])
        
        return f

    def DoS_sum(self,a,aa,R,RR,pexp,smin,smax,dist,C_inst,WA):
        '''Sums the depth of search
        
        Args:
            a (ndarray):
                1D array of semi-major axis bin edge values in AU
            aa (ndarray):
                2D grid of semi-major axis bin edge values in AU
            R (ndarray):
                1D array of planetary radius bin edge values in AU
            RR (ndarray):
                2D grid of planetary radius bin edge values in AU
            pexp (float):
                expected value of geometric albedo
            smin (ndarray):
                1D array of minimum separation values in AU
            smax (ndarray):
                1D array of maximum separation values in AU
            dist (ndarray):
                1D array of stellar distance values in pc
            C_inst (ndarray):
                instrument contrast at working angle
            WA (ndarray):
                working angles in arcseconds
            
        Returns:
            DoS (ndarray):
                2D array of depth of search values summed for input stellar list
        
        '''
        
        DoS = np.zeros((aa.shape[0]-1,aa.shape[1]-1))
        for i in xrange(len(smin)):
            Cs = interpolate.InterpolatedUnivariateSpline(WA, C_inst[i], k=1,ext=3)
            Cmin = np.zeros(a.shape)
            # expected value of Cmin calculations for each separation
            for j in xrange(len(a)):
                if a[j] < smin[i]:
                    Cmin[j] = 1.0
                else:
                    if a[j] > smax[i]:
                        su = smax[i]
                    else:
                        su = a[j]
                    # find expected value of minimum contrast from contrast curve
                    tup = np.sqrt(1.0-(smin[i]/a[j])**2)
                    tlow = np.sqrt(1.0-(su/a[j])**2)
                    f = lambda t,a=a[j],d=dist[i]: Cs(a*np.sqrt(1.0-t**2)/d)
                    val = integrate.quad(f, tlow, tup, epsabs=0,epsrel=1e-3,limit=100)[0]
                    Cmin[j] = val/(tup - tlow)
                    
            CC,RR = np.meshgrid(Cmin,R)
            tmp = self.one_DoS_bins(aa,RR,pexp,smin[i],smax[i],CC)
            DoS += tmp
        
        return DoS

    def find_ck(self,amin,amax,smin,smax,Cmin,pexp,Rexp):
        '''Finds ck metric
        
        Args:
            amin (float):
                minimum semi-major axis value in AU
            amax (float):
                maximum semi-major axis value in AU
            smin (ndarray):
                1D array of minimum separation values in AU
            smax (ndarray):
                1D array of maximum separation values in AU
            Cmin (float):
                minimum contrast value
            pexp (float):
                expected value of geometric albedo
            Rexp (float):
                expected value of planetary radius in AU
            
        Returns:
            ck (ndarray):
                1D array of ck metric
        
        '''
        
        an = 1.0/np.log(amax/amin)
        cg = an*(np.sqrt(1.0-(smax/amax)**2) - np.sqrt(1.0-(smin/amax)**2) + np.log(smax/(np.sqrt(1.0-(smax/amax)**2)+1.0))-np.log(smin/(np.sqrt(1.0-(smin/amax)**2)+1.0)))
        
        # calculate ck
        anp = an/cg 
        # intermediate values
        k1 = np.cos(0.5*(np.pi-np.arcsin(smin/amax)))**4/amax**2
        k2 = np.cos(0.5*(np.pi-np.arcsin(smax/amax)))**4/amax**2
        k3 = np.cos(0.5*np.arcsin(smax/amax))**4/amax**2
        k4 = 27.0/64.0*smax**(-2)
        k5 = np.cos(0.5*np.arcsin(smin/amax))**4/amax**2
        k6 = 27.0/64.0*smin**(-2)
        
        # set up
        z = sympy.Symbol('z', positive=True)
        k = sympy.Symbol('k', positive=True)
        b = sympy.Symbol('b', positive=True)
        # solve
        sol = solve(z**4 - z**3/sympy.sqrt(k) + b**2/(4*k), z)
        # third and fourth roots give valid roots
        # lambdify these roots
        sol3 = sympy.lambdify((k,b), sol[2], "numpy")
        sol4 = sympy.lambdify((k,b), sol[3], "numpy")
        
        # find ck   
        ck = np.zeros(smin.shape)
        kmin = Cmin/(pexp*Rexp**2)
        for i in xrange(len(ck)):
            if smin[i] == smax[i]:
                ck[i] = 0.0
            else:
                # equations to integrate
                al1 = lambda k: sol3(k,smin[i])
                au1 = lambda k: sol4(k,smin[i])
                au2 = lambda k: sol3(k,smax[i])
                al2 = lambda k: sol4(k,smax[i])
                
                f12 = lambda k: anp[i]/(2.0*np.sqrt(k))*(amax - al1(k))
                f23 = lambda k: anp[i]/(2.0*np.sqrt(k))*(au2(k) - al1(k))
                f34 = lambda k: anp[i]/(2.0*np.sqrt(k))*(amax - al2(k) + au2(k) - al1(k))
                f45 = lambda k: anp[i]/(2.0*np.sqrt(k))*(amax - al1(k))
                f56 = lambda k: anp[i]/(2.0*np.sqrt(k))*(au1(k) - al1(k))
                f35 = lambda k: anp[i]/(2.0*np.sqrt(k))*(amax - al2(k) + au2(k) - al1(k))
                f54 = lambda k: anp[i]/(2.0*np.sqrt(k))*(au1(k) - al2(k) + au2(k) - al1(k))
                f46 = lambda k: anp[i]/(2.0*np.sqrt(k))*(au1(k) - al1(k))
                
                if k4[i] < k5[i]:
                    if kmin < k1[i]:
                        ck[i] = integrate.quad(f12,k1[i],k2[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        if k2[i] != k3[i]:
                            ck[i] += integrate.quad(f23,k2[i],k3[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f34,k3[i],k4[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f45,k4[i],k5[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f56,k5[i],k6[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                    elif (kmin > k1[i]) and (kmin < k2[i]):
                        ck[i] = integrate.quad(f12,kmin,k2[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        if k2[i] != k3[i]:
                            ck[i] += integrate.quad(f23,k2[i],k3[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f34,k3[i],k4[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f45,k4[i],k5[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f56,k5[i],k6[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                    elif (kmin > k2[i]) and (kmin < k3[i]):
                        ck[i] = integrate.quad(f23,kmin,k3[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f34,k3[i],k4[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f45,k4[i],k5[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f56,k5[i],k6[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                    elif (kmin > k3[i]) and (kmin < k4[i]):
                        ck[i] = integrate.quad(f34,kmin,k4[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f45,k4[i],k5[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f56,k5[i],k6[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                    elif (kmin > k4[i]) and (kmin < k5[i]):
                        ck[i] = integrate.quad(f45,kmin,k5[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f56,k5[i],k6[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                    elif (kmin < k6[i]):
                        ck[i] = integrate.quad(f56,kmin,k6[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                    else:
                        ck[i] = 0.0
                else:
                    if kmin < k1[i]:
                        ck[i] = integrate.quad(f12,k1[i],k2[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        if k2[i] != k3[i]:
                            ck[i] += integrate.quad(f23,k2[i],k3[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f35,k3[i],k5[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f54,k5[i],k4[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f46,k4[i],k6[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                    elif (kmin > k1[i]) and (kmin < k2[i]):
                        ck[i] = integrate.quad(f12,kmin,k2[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        if k2[i] != k3[i]:
                            ck[i] += integrate.quad(f23,k2[i],k3[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f35,k3[i],k5[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f54,k5[i],k4[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f46,k4[i],k6[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                    elif (kmin > k2[i]) and (kmin < k3[i]):
                        ck[i] = integrate.quad(f23,kmin,k3[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f35,k3[i],k5[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f54,k5[i],k4[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f46,k4[i],k6[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                    elif (kmin > k3[i]) and (kmin < k5[i]):
                        ck[i] = integrate.quad(f35,kmin,k5[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f54,k5[i],k4[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f46,k4[i],k6[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                    elif (kmin > k5[i]) and (kmin < k4[i]):
                        ck[i] = integrate.quad(f54,kmin,k4[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                        ck[i] += integrate.quad(f46,k4[i],k6[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                    elif (kmin < k6[i]):
                        ck[i] = integrate.quad(f46,kmin,k6[i],limit=50,epsabs=0,epsrel=1e-4)[0]
                    else:
                        ck[i] = 0.0
                
        return ck

    def select_obs(self,t0,maxTime,ck):
        '''Selects stars for observation using ortools
        
        Args:
            t0 (ndarray):
                1D array of integration times in days
            maxTime (float):
                total observation time allotted in days
            ck (ndarray):
                1D array of ck metric
        
        Returns:
            sInds (ndarray):
                1D array of star indices selected for observation
        
        '''

        #set up solver
        solver = pywraplp.Solver('SolveIntegerProblem',pywraplp.Solver.CBC_MIXED_INTEGER_PROGRAMMING)
        #need one var per state
        xs = [ solver.IntVar(0.0,1.0, 'x'+str(j)) for j in range(len(ck)) ]
        #constraint is x_i*t_i < maxtime
        constraint1 = solver.Constraint(-solver.infinity(),maxTime)
        for j,x in enumerate(xs):
            constraint1.SetCoefficient(x, t0[j])
        #objective is max x_i*comp_i
        objective = solver.Objective()
        for j,x in enumerate(xs):
            objective.SetCoefficient(x, ck[j])
        objective.SetMaximization()
        res = solver.Solve()
        print 'Objective function value: %r' % (solver.Objective().Value())
        #collect result
        xs2 = np.array([x.solution_value() for x in xs])
        
        # observed star indices for depth of search calculations
        sInds = np.where(xs2>0)[0]
        
        return sInds


    def plot_dos(self,targ,name,path=None):
        '''Plots depth of search as a filled contour plot with contour lines
        
        Args:
            targ (str):
                string indicating which key to access from depth of search 
                result dictionary
            name (str):
                string indicating what to put in title of figure
            path (str):
                desired path to save figure (pdf, optional)
        
        '''
        
        acents = 0.5*(self.result['aedges'][1:]+self.result['aedges'][:-1])
        a = np.hstack((self.result['aedges'][0],acents,self.result['aedges'][-1]))
        a = np.around(a,4)
        Rcents = 0.5*(self.result['Redges'][1:]+self.result['Redges'][:-1])
        R = np.hstack((self.result['Redges'][0],Rcents,self.result['Redges'][-1]))
        R = np.around(R,4)
        DoS = self.result['DoS'][targ]
        # extrapolate to left-most boundary
        tmp = DoS[:,0] + (a[0]-a[1])*((DoS[:,1]-DoS[:,0])/(a[2]-a[1]))
        DoS = np.insert(DoS, 0, tmp, axis=1)
        # extrapolate to right-most boundary
        tmp = DoS[:,-1] + (a[-1]-a[-2])*((DoS[:,-1]-DoS[:,-2])/(a[-2]-a[-3]))
        DoS = np.insert(DoS, -1, tmp, axis=1)
        # extrapolate to bottom-most boundary
        tmp = DoS[0,:] + (R[0]-R[1])*((DoS[1,:]-DoS[0,:])/(R[2]-R[1]))
        DoS = np.insert(DoS, 0, tmp, axis=0)
        # extrapolate to upper-most boundary
        tmp = DoS[-1,:] + (R[-1]-R[-2])*((DoS[-1,:]-DoS[-2,:])/(R[-2]-R[-3]))
        DoS = np.insert(DoS, -1, tmp, axis=0)
        DoS = np.ma.masked_where(DoS<=0.0, DoS)
        fig = plt.figure()
        ax = fig.add_subplot(111)
        cs = ax.contourf(a,R,DoS,locator=ticker.LogLocator())
        cs2 = ax.contour(a,R,DoS,levels=cs.levels[1:],colors='k')
        ax.set_xscale('log')
        ax.set_yscale('log')
        ax.set_xlabel('a (AU)')
        ax.set_ylabel('R ($R_\oplus$)')
        ax.set_title('Depth of Search - '+name+' ('+str(self.result['NumObs'][targ])+')')
        cbar = fig.colorbar(cs)
        ax.clabel(cs2, fmt=ticker.LogFormatterMathtext(), colors='k')
        if path != None:
            fig.savefig(path, format='pdf', dpi=600, bbox_inches='tight', pad_inches=0.1)
        plt.show()

    def plot_nplan(self,targ,name,path=None):
        '''Plots depth of search convolved with occurrence rates as a filled 
        contour plot with contour lines
        
        Args:
            targ (str):
                string indicating which key to access from depth of search 
                result dictionary
            name (str):
                string indicating what to put in title of figure
            path (str):
                desired path to save figure (pdf, optional)
        
        '''
        
        acents = 0.5*(self.result['aedges'][1:]+self.result['aedges'][:-1])
        a = np.hstack((self.result['aedges'][0],acents,self.result['aedges'][-1]))
        a = np.around(a,4)
        Rcents = 0.5*(self.result['Redges'][1:]+self.result['Redges'][:-1])
        R = np.hstack((self.result['Redges'][0],Rcents,self.result['Redges'][-1]))
        R = np.around(R,4)
        DoS_occ = self.result['DoS_occ'][targ]
        # extrapolate to left-most boundary
        tmp = DoS_occ[:,0] + (a[0]-a[1])*((DoS_occ[:,1]-DoS_occ[:,0])/(a[2]-a[1]))
        DoS_occ = np.insert(DoS_occ, 0, tmp, axis=1)
        # extrapolate to right-most boundary
        tmp = DoS_occ[:,-1] + (a[-1]-a[-2])*((DoS_occ[:,-1]-DoS_occ[:,-2])/(a[-2]-a[-3]))
        DoS_occ = np.insert(DoS_occ, -1, tmp, axis=1)
        # extrapolate to bottom-most boundary
        tmp = DoS_occ[0,:] + (R[0]-R[1])*((DoS_occ[1,:]-DoS_occ[0,:])/(R[2]-R[1]))
        DoS_occ = np.insert(DoS_occ, 0, tmp, axis=0)
        # extrapolate to upper-most boundary
        tmp = DoS_occ[-1,:] + (R[-1]-R[-2])*((DoS_occ[-1,:]-DoS_occ[-2,:])/(R[-2]-R[-3]))
        DoS_occ = np.insert(DoS_occ, -1, tmp, axis=0)
        DoS_occ = np.ma.masked_where(DoS_occ <= 0.0, DoS_occ)
        fig = plt.figure()
        ax = fig.add_subplot(111)
        cs = ax.contourf(a,R,DoS_occ,locator=ticker.LogLocator())
        cs2 = ax.contour(a,R,DoS_occ,levels=cs.levels[1:],colors='k')
        ax.set_xscale('log')
        ax.set_yscale('log')
        ax.set_xlabel('a (AU)')
        ax.set_ylabel('R ($R_\oplus$)')
        ax.set_title('Number of Planets - '+name+' ('+str(self.result['NumObs'][targ])+')')
        cbar = fig.colorbar(cs)
        ax.clabel(cs2, fmt=ticker.LogFormatterMathtext(), colors='k')
        if path != None:
            fig.savefig(path, format='pdf', dpi=600, bbox_inches='tight', pad_inches=0.1)
        plt.show()
    
    def save_results(self, path):
        '''Saves results and outspec dictionaries to disk
        
        Args:
            path (str):
                string containing path for saved results
        
        '''
        
        x = {'Results': self.result, 'outspec': self.outspec}
        with open(path,'wb') as f:
            pickle.dump(x, f)
            print 'Results saved as '+path
        
    def save_json(self, path):
        '''Saves json file used to generate results to disk
        
        Args:
            path (str):
                string containing directory path for file
        
        '''
        
        self.sim.genOutSpec(tofile=path)
        print 'json script saved as '+path
        
    def save_csvs(self, directory):
        '''Saves results as individual csv files to disk
        
        Args:
            directory (str):
                string containing directory path for files
                
        '''
        
        # save aedges and Redges first
        np.savetxt(directory+'/aedges.csv', self.result['aedges'], delimiter=', ')
        np.savetxt(directory+'/Redges.csv', self.result['Redges'], delimiter=', ')
        
        # save NumObs
        keys = self.result['NumObs'].keys()
        x = []
        h = ', '
        for i in xrange(len(keys)):
            x.append(self.result['NumObs'][keys[i]])
            h += keys[i]+', '
        h += '\n'
        np.savetxt(directory+'/NumObs.csv', x, delimiter=', ', newline=', ', header=h)
        
        # save DoS
        for key in self.result['DoS'].keys():
            np.savetxt(directory+'/DoS_'+key+'.csv', self.result['DoS'][key], delimiter=', ')
        
        # save occ_rates
        for key in self.result['occ_rates'].keys():
            np.savetxt(directory+'/occ_rates_'+key+'.csv', self.result['occ_rates'][key], delimiter=', ')
        
        # save DoS_occ
        for key in self.result['DoS_occ'].keys():
            np.savetxt(directory+'/DoS_occ_'+key+'.csv', self.result['DoS_occ'][key], delimiter=', ')
