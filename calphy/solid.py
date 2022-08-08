"""
calphy: a Python library and command line interface for automated free
energy calculations.

Copyright 2021  (c) Sarath Menon^1, Yury Lysogorskiy^2, Ralf Drautz^2
^1: Max Planck Institut für Eisenforschung, Dusseldorf, Germany 
^2: Ruhr-University Bochum, Bochum, Germany

calphy is published and distributed under the Academic Software License v1.0 (ASL). 
calphy is distributed in the hope that it will be useful for non-commercial academic research, 
but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  
calphy API is published and distributed under the BSD 3-Clause "New" or "Revised" License
See the LICENSE FILE for more details. 

More information about the program can be found in:
Menon, Sarath, Yury Lysogorskiy, Jutta Rogal, and Ralf Drautz.
“Automated Free Energy Calculation from Atomistic Simulations.” Physical Review Materials 5(10), 2021
DOI: 10.1103/PhysRevMaterials.5.103801

For more information contact:
sarath.menon@ruhr-uni-bochum.de/yury.lysogorskiy@icams.rub.de
"""

import numpy as np
import yaml
import copy
import sys

import pyscal.traj_process as ptp
from calphy.integrators import *
import calphy.lattice as pl
import calphy.helpers as ph
import calphy.phase as cph
from calphy.errors import *

class Solid(cph.Phase):
    """
    Class for free energy calculation with solid as the reference state

    Parameters
    ----------
    options : dict
        dict of input options
    
    kernel : int
        the index of the calculation that should be run from
        the list of calculations in the input file

    simfolder : string
        base folder for running calculations

    """
    def __init__(self, calculation=None, simfolder=None):

        #call base class
        super().__init__(calculation=calculation,
        simfolder=simfolder)

    def dump_current_snapshot(self, lmp, filename):
        """
        """
        lmp.command("dump              2 all custom 1 %s id type mass x y z vx vy vz"%(filename))
        lmp.command("run               0")
        lmp.command("undump            2")

    def check_if_melted(self, lmp, filename):
        """
        """
        solids = ph.find_solid_fraction(os.path.join(self.simfolder, filename))
        if (solids/lmp.natoms < self.calc.tolerance.solid_fraction):
            lmp.close()
            raise MeltedError("System melted, increase size or reduce temp!\n Solid detection algorithm only works with BCC/FCC/HCP/SC/DIA. Detection algorithm can be turned off by setting conv:\n solid_frac: 0")


    def fix_nose_hoover(self, lmp, temp_start_factor=1.0, temp_end_factor=1.0, press_start_factor=1.0, press_end_factor=1.0):
        """
        Fix Nose-Hoover thermostat and barostat

        Parameters
        ----------
        None

        Returns
        -------
        None
        """
        lmp.command("fix              1 all npt temp %f %f %f %s %f %f %f"%(temp_start_factor*self.calc._temperature, 
                        temp_end_factor*self.calc._temperature, self.calc.md.equilibration_thermostat_damping, 
                        self.iso, press_start_factor*self.calc._pressure, press_end_factor*self.calc._pressure, self.calc.md.equilibration_barostat_damping))

    def fix_berendsen(self, lmp, temp_start_factor=1.0, temp_end_factor=1.0, press_start_factor=1.0, press_end_factor=1.0):
        """
        Fix Nose-Hoover thermostat and barostat

        Parameters
        ----------
        None

        Returns
        -------
        None
        """
        lmp.command("fix              1a all nve")
        lmp.command("fix              1b all temp/berendsen %f %f %f"%(temp_start_factor*self.calc._temperature, temp_end_factor*self.calc._temperature, self.calc.md.equilibration_thermostat_damping))
        lmp.command("fix              1c all press/berendsen %s %f %f %f"%(self.iso, press_start_factor*self.calc._pressure, press_end_factor*self.calc._pressure, self.calc.md.equilibration_barostat_damping))

    def unfix_nose_hoover(self, lmp):
        """
        Fix Nose-Hoover thermostat and barostat

        Parameters
        ----------
        None

        Returns
        -------
        None
        """
        lmp.command("unfix            1")

    def unfix_berendsen(self, lmp):
        """
        Fix Nose-Hoover thermostat and barostat

        Parameters
        ----------
        None

        Returns
        -------
        None
        """
        lmp.command("unfix            1a")
        lmp.command("unfix            1b")
        lmp.command("unfix            1c")        


    def run_zero_pressure_equilibration(self, lmp):
        """
        Run a zero pressure equilibration

        Parameters
        ----------
        lmp: LAMMPS object

        Returns
        -------
        None

        Notes
        -----
        Each method should close all the fixes. Run a small eqbr routine to achieve zero pressure        
        """
        #set velocity
        lmp.command("velocity         all create %f %d"%(self.calc._temperature, np.random.randint(0, 10000)))
        
        #apply fixes depending on thermostat/barostat
        if self.calc.md.equilibration_control == "nose-hoover":
            self.fix_nose_hoover(lmp)
        else:
            self.fix_berendsen(lmp)
        #start thermo logging
        lmp.command("thermo_style     custom step pe press vol etotal temp lx ly lz")
        lmp.command("thermo           10")
        
        #run MD
        lmp.command("run              %d"%int(self.calc.md.n_small_steps)) 
        
        #remove fixes
        if self.calc.md.equilibration_control == "nose-hoover":
            self.unfix_nose_hoover(lmp)
        else:
            self.unfix_berendsen(lmp)



    def run_finite_pressure_equilibration(self, lmp):
        """
        Run a finite pressure equilibration

        Parameters
        ----------
        lmp: LAMMPS object

        Returns
        -------
        None
        
        Notes
        -----
        Each method should close all the fixes. Run a equilibration routine to reach the given finite pressure.
        The pressure is implemented in one fix, while temperature is gradually ramped. 
        The thermostat can work faster than barostat, which means that the structure will melt before the pressure is scaled, this ramping
        can prevent the issue.         
        """
        #create velocity
        lmp.command("velocity         all create %f %d"%(0.25*self.calc._temperature, np.random.randint(0, 10000)))

        #for Nose-Hoover thermo/baro combination
        if self.calc.md.equilibration_control == "nose-hoover":
            #Cycle 1: 0.25-0.5 temperature, full pressure
            self.fix_nose_hoover(lmp, temp_start_factor=0.25, temp_end_factor=0.5)
            lmp.command("thermo_style     custom step pe press vol etotal temp")
            lmp.command("thermo           10")
            lmp.command("run              %d"%int(self.calc.md.n_small_steps)) 
            self.unfix_nose_hoover(lmp)

            #Cycle 2: 0.5-1.0 temperature, full pressure
            self.fix_nose_hoover(lmp, temp_start_factor=0.5, temp_end_factor=1.0)
            lmp.command("run              %d"%int(self.calc.md.n_small_steps)) 
            self.unfix_nose_hoover(lmp)

            #Cycle 3: full temperature, full pressure
            self.fix_nose_hoover(lmp)
            lmp.command("run              %d"%int(self.calc.md.n_small_steps))
            self.unfix_nose_hoover(lmp)
        
        else:
            #Cycle 1: 0.25-0.5 temperature, full pressure
            self.fix_berendsen(lmp, temp_start_factor=0.25, temp_end_factor=0.5)
            lmp.command("thermo_style     custom step pe press vol etotal temp")
            lmp.command("thermo           10")
            lmp.command("run              %d"%int(self.calc.md.n_small_steps))                    
            self.unfix_berendsen(lmp)

            #Cycle 2: 0.5-1.0 temperature, full pressure
            self.fix_berendsen(lmp, temp_start_factor=0.5, temp_end_factor=1.0)
            lmp.command("run              %d"%int(self.calc.md.n_small_steps))                    
            self.unfix_berendsen(lmp)

            #Cycle 3: full temperature, full pressure
            self.fix_berendsen(lmp)
            lmp.command("run              %d"%int(self.calc.md.n_small_steps))                    
            self.unfix_berendsen(lmp)

    def run_pressure_convergence(self, lmp):
        """
        Run a pressure convergence routine

        Parameters
        ----------
        lmp: LAMMPS object

        Returns
        -------
        None

        Notes
        -----
        Take the equilibrated structure and rigorously check for pressure convergence.
        The cycle is stopped when the average pressure is within the given cutoff of the target pressure.
        """

        #apply fixes
        if self.calc.md.equilibration_control == "nose-hoover":
            self.fix_nose_hoover(lmp)
        else:
            self.fix_berendsen(lmp)


        lmp.command("fix              2 all ave/time %d %d %d v_mlx v_mly v_mlz v_mpress file avg.dat"%(int(self.calc.md.n_every_steps),
            int(self.calc.md.n_repeat_steps), int(self.calc.md.n_every_steps*self.calc.md.n_repeat_steps)))
        
        laststd = 0.00
        converged = False

        for i in range(int(self.calc.md.n_cycles)):
            lmp.command("run              %d"%int(self.calc.md.n_small_steps))
            ncount = int(self.calc.md.n_small_steps)//int(self.calc.md.n_every_steps*self.calc.md.n_repeat_steps)
            #now we can check if it converted
            file = os.path.join(self.simfolder, "avg.dat")
            lx, ly, lz, ipress = np.loadtxt(file, usecols=(1, 2, 3, 4), unpack=True)
            
            lxpc = ipress
            mean = np.mean(lxpc)
            std = np.std(lxpc)
            volatom = np.mean((lx*ly*lz)/self.natoms)
            self.logger.info("At count %d mean pressure is %f with %f vol/atom"%(i+1, mean, volatom))
            
            if (np.abs(mean - self.calc._pressure)) < self.calc.tolerance.pressure:

                #process other means
                self.lx = np.round(np.mean(lx[-ncount+1:]), decimals=3)
                self.ly = np.round(np.mean(ly[-ncount+1:]), decimals=3)
                self.lz = np.round(np.mean(lz[-ncount+1:]), decimals=3)
                self.volatom = volatom
                self.vol = self.lx*self.ly*self.lz
                self.logger.info("finalized vol/atom %f at pressure %f"%(self.volatom, mean))
                self.logger.info("Avg box dimensions x: %f, y: %f, z:%f"%(self.lx, self.ly, self.lz))
                converged = True
                break
            laststd = std
        
        if not converged:
            lmp.close()
            raise ValueError("Pressure did not converge after MD runs, maybe change lattice_constant and try?")

        #unfix thermostat and barostat
        if self.calc.md.equilibration_control == "nose-hoover":
            self.unfix_nose_hoover(lmp)
        else:
            self.unfix_berendsen(lmp)

        lmp.command("unfix            2")


    def run_constrained_pressure_convergence(self, lmp):
        """
        """
        lmp.command("fix              1 all nvt temp %f %f %f"%(self.calc._temperature, self.calc._temperature, self.calc.md.thermostat_damping))
        lmp.command("velocity         all create %f %d"%(self.calc._temperature, np.random.randint(0, 10000)))
        lmp.command("thermo_style     custom step pe press vol etotal temp lx ly lz")
        lmp.command("thermo           10")
        
        #this is when the averaging routine starts
        lmp.command("fix              2 all ave/time %d %d %d v_mlx v_mly v_mlz v_mpress file avg.dat"%(int(self.calc.md.n_every_steps),
            int(self.calc.md.n_repeat_steps), int(self.calc.md.n_every_steps*self.calc.md.n_repeat_steps)))

        lastmean = 100000000
        converged = False
        for i in range(int(self.calc.md.n_cycles)):
            lmp.command("run              %d"%int(self.calc.md.n_small_steps))
            ncount = int(self.calc.md.n_small_steps)//int(self.calc.md.n_every_steps*self.calc.md.n_repeat_steps)
            #now we can check if it converted
            file = os.path.join(self.simfolder, "avg.dat")
            lx, ly, lz, ipress = np.loadtxt(file, usecols=(1, 2, 3, 4), unpack=True)
            
            lxpc = ipress
            mean = np.mean(lxpc)
            if (np.abs(mean - lastmean)) < self.calc.tolerance.pressure:
                #here we actually have to set the pressure
                self.calc._pressure = mean
                std = np.std(lxpc)
                volatom = np.mean((lx*ly*lz)/self.natoms)
                self.logger.info("At count %d mean pressure is %f with %f vol/atom"%(i+1, mean, volatom))
                self.lx = np.round(np.mean(lx[-ncount+1:]), decimals=3)
                self.ly = np.round(np.mean(ly[-ncount+1:]), decimals=3)
                self.lz = np.round(np.mean(lz[-ncount+1:]), decimals=3)
                self.volatom = volatom
                self.vol = self.lx*self.ly*self.lz
                self.logger.info("finalized vol/atom %f at pressure %f"%(self.volatom, mean))
                self.logger.info("Avg box dimensions x: %f, y: %f, z:%f"%(self.lx, self.ly, self.lz))
                #now run for msd
                converged = True
                break
            lastmean = mean
        lmp.command("unfix            1")
        lmp.command("unfix            2")

        if not converged:
            lmp.close()
            raise ValueError("pressure did not converge")


    def run_spring_constant_convergence(self, lmp):
        """
        """
        lmp.command("fix              3 all nvt temp %f %f %f"%(self.calc._temperature, self.calc._temperature, self.calc.md.thermostat_damping))
        
        #apply fix
        lmp = ph.compute_msd(lmp, self.calc)
        
        if ph.check_if_any_is_none(self.calc.spring_constants):
            #similar averaging routine
            laststd = 0.00
            for i in range(self.calc.md.n_cycles):
                lmp.command("run              %d"%int(self.calc.md.n_small_steps))
                ncount = int(self.calc.md.n_small_steps)//int(self.calc.md.n_every_steps*self.calc.md.n_repeat_steps)
                #now we can check if it converted
                file = os.path.join(self.simfolder, "msd.dat")
                quant = np.loadtxt(file, usecols=(1,), unpack=True)[-ncount+1:]
                quant = 3*kb*self.calc._temperature/quant
                #self.logger.info(quant)
                mean = np.mean(quant)
                std = np.std(quant)
                self.logger.info("At count %d mean k is %f std is %f"%(i+1, mean, std))
                if (np.abs(laststd - std) < self.calc.tolerance.spring_constant):
                    #now reevaluate spring constants
                    k = []
                    for i in range(self.calc.n_elements):
                        quant = np.loadtxt(file, usecols=(i+1, ), unpack=True)[-ncount+1:]
                        quant = 3*kb*self.calc._temperature/quant
                        k.append(np.round(np.mean(quant), decimals=2))

                    #first replace any provided values with user values
                    if ph.check_if_any_is_not_none(self.calc.spring_constants):
                        spring_constants = copy.copy(self.calc.spring_constants)
                        k = ph.replace_nones(spring_constants, k, logger=self.logger)
                    
                    #add sanity checks
                    k = ph.validate_spring_constants(k, logger=self.logger)
                    
                    #now save
                    self.k = k

                    self.logger.info("finalized sprint constants")
                    self.logger.info(self.k)
                    break
                laststd = std

        else:
            if not (len(self.calc.spring_constants) == self.calc.n_elements):
                raise ValueError("Spring constant input length should be same as number of elements, spring constant length %d, # elements %d"%(len(self.calc.spring_constants), self.calc.n_elements))

            #still run a small NVT cycle
            lmp.command("run              %d"%int(self.calc.md.n_small_steps))
            self.k = self.calc.spring_constants
            self.logger.info("Used user input sprint constants")
            self.logger.info(self.k)

        lmp.command("unfix         3")

    def run_averaging(self):
        """
        Run averaging routine

        Parameters
        ----------
        None

        Returns
        -------
        None

        Notes
        -----
        Run averaging routine using LAMMPS. Starting from the initial lattice two different routines can
        be followed:
        If pressure is specified, MD simulations are run until the pressure converges within the given
        threshold value.
        If `fix_lattice` option is True, then the input structure is used as it is and the corresponding pressure
        is calculated.
        At the end of the run, the averaged box dimensions are calculated. 
        """
        lmp = ph.create_object(self.cores, self.simfolder, self.calc.md.timestep)

        #set up structure
        lmp = ph.create_structure(lmp, self.calc)

        #set up potential
        if self.calc.potential_file is None:
            lmp = ph.set_potential(lmp, self.calc)
        else:
            lmp.command("include %s"%self.calc.potential_file)

        #add some computes
        lmp.command("variable         mvol equal vol")
        lmp.command("variable         mlx equal lx")
        lmp.command("variable         mly equal ly")
        lmp.command("variable         mlz equal lz")
        lmp.command("variable         mpress equal press")

        #Run if a constrained lattice is not needed
        if not self.calc._fix_lattice:
            if self.calc._pressure == 0:
                self.run_zero_pressure_equilibration(lmp)
            else:
                self.run_finite_pressure_equilibration(lmp)


            #this is when the averaging routine starts
            self.run_pressure_convergence(lmp)

            #dump snapshot and check if melted
            self.dump_current_snapshot(lmp, "traj.equilibration_stage1.dat")
            self.check_if_melted(lmp, "traj.equilibration_stage1.dat")
        
        #run if a constrained lattice is used
        else:
            #routine in which lattice constant will not varied, but is set to a given fixed value
            self.run_constrained_pressure_convergence(lmp)

        #start MSD calculation routine
        #there two possibilities here - if spring constants are provided, use it. If not, calculate it
        self.run_spring_constant_convergence(lmp)

        #check for melting
        self.dump_current_snapshot(lmp, "traj.equilibration_stage2.dat")
        self.check_if_melted(lmp, "traj.equilibration_stage2.dat")

        #close object and process traj
        lmp.close()
        self.process_traj("traj.equilibration_stage2.dat", "conf.equilibration.dump")



    def run_integration(self, iteration=1):
        """
        Run integration routine

        Parameters
        ----------
        iteration : int, optional
            iteration number for running independent iterations

        Returns
        -------
        None

        Notes
        -----
        Run the integration routine where the initial and final systems are connected using
        the lambda parameter. See algorithm 4 in publication.
        """
        lmp = ph.create_object(self.cores, self.simfolder, self.calc.md.timestep)

        #read in the conf file
        conf = os.path.join(self.simfolder, "conf.equilibration.dump")
        lmp = ph.read_dump(lmp, conf, species=self.calc.n_elements)

        #set up potential
        if self.calc.potential_file is None:
            lmp = ph.set_potential(lmp, self.calc)
        else:
            lmp.command("include %s"%self.calc.potential_file)

        #remap the box to get the correct pressure
        lmp = ph.remap_box(lmp, self.lx, self.ly, self.lz)

        #create groups - each species belong to one group
        for i in range(self.calc.n_elements):
            lmp.command("group  g%d type %d"%(i+1, i+1))

        #get counts of each group
        for i in range(self.calc.n_elements):
            lmp.command("variable   count%d equal count(g%d)"%(i+1, i+1))

        #initialise everything
        lmp.command("run               0")

        #apply initial fixes
        lmp.command("fix               f1 all nve")
        
        #apply fix for each spring
        #TODO: Add option to select function
        for i in range(self.calc.n_elements):
            lmp.command("fix               ff%d g%d ti/spring 10.0 100 100 function 2"%(i+1, i+1))
        
        #apply temp fix
        lmp.command("fix               f3 all langevin %f %f %f %d zero yes"%(self.calc._temperature, self.calc._temperature, self.calc.md.thermostat_damping, 
                                        np.random.randint(0, 10000)))

        #compute com and apply to fix
        lmp.command("compute           Tcm all temp/com")
        lmp.command("fix_modify        f3 temp Tcm")

        lmp.command("variable          step    equal step")
        lmp.command("variable          dU1      equal pe/atoms")
        for i in range(self.calc.n_elements):
            lmp.command("variable          dU%d      equal f_ff%d/v_count%d"%(i+2, i+1, i+1))
        
        lmp.command("variable          lambda  equal f_ff1[1]")

        #add thermo command to force variable evaluation
        lmp.command("thermo_style      custom step pe c_Tcm")
        lmp.command("thermo            10000")

        #Create velocity
        lmp.command("velocity          all create %f %d mom yes rot yes dist gaussian"%(self.calc._temperature, np.random.randint(0, 10000)))

        #reapply 
        for i in range(self.calc.n_elements):
            lmp.command("fix               ff%d g%d ti/spring %f %d %d function 2"%(i+1, i+1, self.k[i], 
                self.calc._n_switching_steps, self.calc.n_equilibration_steps))

        #Equilibriate structure
        lmp.command("run               %d"%self.calc.n_equilibration_steps)
        
        #write out energy
        str1 = "fix f4 all print 1 \"${dU1} "
        str2 = []
        for i in range(self.calc.n_elements):
            str2.append("${dU%d}"%(i+2))
        str2.append("${lambda}\"")
        str2 = " ".join(str2)
        str3 = " screen no file forward_%d.dat"%iteration
        command = str1 + str2 + str3
        lmp.command(command)

        #Forward switching over ts steps
        lmp.command("run               %d"%self.calc._n_switching_steps)
        lmp.command("unfix             f4")

        #Equilibriate
        lmp.command("run               %d"%self.calc.n_equilibration_steps)

        #write out energy
        str1 = "fix f4 all print 1 \"${dU1} "
        str2 = []
        for i in range(self.calc.n_elements):
            str2.append("${dU%d}"%(i+2))
        str2.append("${lambda}\"")
        str2 = " ".join(str2)
        str3 = " screen no file backward_%d.dat"%iteration
        command = str1 + str2 + str3
        lmp.command(command)

        #Reverse switching over ts steps
        lmp.command("run               %d"%self.calc._n_switching_steps)
        lmp.command("unfix             f4")

        #close object
        lmp.close()


    def thermodynamic_integration(self):
        """
        Calculate free energy after integration step

        Parameters
        ----------
        None

        Returns
        -------
        None

        Notes
        -----
        Calculates the final work, energy dissipation and free energy by
        matching with Einstein crystal
        """
        f1 = get_einstein_crystal_fe(self.calc._temperature, 
            self.natoms, self.calc.mass, 
            self.vol, self.k, self.concentration)
        w, q, qerr = find_w(self.simfolder, 
            nelements=self.calc.n_elements, 
            concentration=self.concentration, nsims=self.calc.n_iterations, 
            full=True, solid=True)
        
        self.fref = f1
        self.w = w
        self.ferr = qerr

        #add pressure contribution if required
        if self.calc._pressure != 0:
            p = self.calc._pressure/(10000*160.21766208)
            v = self.vol/self.natoms
            self.pv = p*v
        else:
            self.pv = 0 

        #calculate final free energy
        self.fe = self.fref + self.w + self.pv

        