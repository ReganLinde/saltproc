from saltproc import Depcode
from saltproc import Simulation
from saltproc import Materialflow
from saltproc import Process
# from depcode import Depcode
# from simulation import Simulation
# from materialflow import Materialflow
import os
import copy
import tables as tb
import json
from collections import OrderedDict
import gc
import networkx as nx
import pydotplus


input_path = os.path.dirname(os.path.abspath(__file__))
# input_path = '/home/andrei2/Desktop/git/saltproc/develop/saltproc'
spc_inp_file = os.path.join(input_path, '../examples/tap_objects.json')
dot_inp_file = os.path.join(input_path, '../examples/tap.dot')
template_file = os.path.join(input_path, '../examples/tap.serpent')  # user's input file
# [line number where insrt link to geometry file, 1st geo, 2nd geo, etc
geo_file = [2,  # number of line in Serpent input where to insert 'include geo'
            os.path.join(input_path, '../examples/geometry/347_base.ini'),
            os.path.join(input_path, '../examples/geometry/406.ini'),
            os.path.join(input_path, '../examples/geometry/427.ini'),
            os.path.join(input_path, '../examples/geometry/505.ini'),
            os.path.join(input_path, '../examples/geometry/576.ini'),
            os.path.join(input_path, '../examples/geometry/633.ini'),
            os.path.join(input_path, '../examples/geometry/681.ini'),
            os.path.join(input_path, '../examples/geometry/840.ini'),
            os.path.join(input_path, '../examples/geometry/880.ini'),
            os.path.join(input_path, '../examples/geometry/900.ini'),
            os.path.join(input_path, '../examples/geometry/988.ini'),
            os.path.join(input_path, '../examples/geometry/1126.ini'),
            os.path.join(input_path, '../examples/geometry/1338.ini'),
            os.path.join(input_path, '../examples/geometry/1498.ini'),
            os.path.join(input_path, '../examples/geometry/1668_all.ini')]

input_file = os.path.join(input_path, 'data/saltproc_tap')
iter_matfile = os.path.join(input_path, 'data/saltproc_mat')
db_file = os.path.join(input_path, 'data/db_saltproc.h5')
compression_prop = tb.Filters(complevel=9, complib='blosc', fletcher32=True)
# executable path of Serpent
exec_path = 'sss2'
# exec_path = '/projects/sciteam/bahg/serpent/src2.1.31/sss2'  # BW
# exec_path = '/apps/exp_ctl/easybuild/software/serpent/2.1.31-goolf-5.5.7/bin/sss2'

adjust_geo = True
restart_flag = False
pc_type = 'falcon'  # 'bw', 'falcon', 'pc'
# Number of cores and nodes to use in cluster
cores = 12  # doesn't used on Falcon (grabbing it from PBS) 32
nodes = 1  # doesn't use on Falcon (grabbing it from PBS)  16
steps = 2  # 2000
# Monte Carlo method parameters
neutron_pop = 50   # 5000; 15 000 400 200; 10 000, 400, 100: 35pcm; 15 000, 400, 200: 30pcm
active_cycles = 20   # 160; 20; 50'000, 400, 250: 16pcm
inactive_cycles = 20 # 100
# Define materials (should read from input file)
core_massflow_rate = 9.92E+6  # g/s


def read_processes_from_input():
    processes = OrderedDict()
    with open(spc_inp_file) as f:
        j = json.load(f)
        # print(j)
        for mat, value in j.items():
            processes[mat] = OrderedDict()
            for obj_name, obj_data in j[mat]['extraction_processes'].items():
                processes[mat][obj_name] = Process(**obj_data)
        """print(processes)
        print('\nFuel mat', processes['fuel'])
        print('\nPoison rods mat', processes['ctrlPois'])
        print('\nProcess objects attributes:')
        print("Sparger efficiency ", processes['fuel']['sparger'].efficiency)
        # print("Ni filter efficiency", processes['nickel_filter'].efficiency)
        print(processes['fuel']['sparger'].mass_flowrate)
        print(processes['ctrlPois']['removal_tb_dy'].efficiency)"""
        gc.collect()
        return processes


def read_feeds_from_input():
    feeds = OrderedDict()
    with open(spc_inp_file) as f:
        j = json.load(f)
        # print(j['feeds'])
        for mat, val in j.items():
            feeds[mat] = OrderedDict()
            for obj_name, obj_data in j[mat]['feeds'].items():
                # print(obj_data)
                nucvec = obj_data['comp']
                feeds[mat][obj_name] = Materialflow(nucvec)
                feeds[mat][obj_name].mass = obj_data['mass']
                feeds[mat][obj_name].density = obj_data['density']
                feeds[mat][obj_name].vol = obj_data['volume']
        # print(feeds['fuel']['leu'])
        # print(feeds['ctrlPois']['pure_gd'])
        # print(feeds['fuel']['leu'].print_attr())
        return feeds


def read_dot(dotf):
    """ Reads directed graph from *.dot files
    Returns:
    --------
    digraph: networkx.classes.multidigraph.MultiDiGraph Object
    """
    graph_pydot = pydotplus.graph_from_dot_file(dotf)
    digraph = nx.drawing.nx_pydot.from_pydot(graph_pydot)
    mat_name = digraph.__str__()
    # iterate over all possible paths between 'core_outlet' and 'core_inlet'
    paths_list = []
    all_simple_paths = nx.all_simple_paths(digraph,
                                           source='core_outlet',
                                           target='core_inlet')
    for path in all_simple_paths:
        paths_list.append(path)
    return mat_name, paths_list


def reprocessing(mat):
    """ Applies reprocessing scheme to selected material

    Parameters:
    -----------
    mat: Materialflow object`
        Material data right after irradiation in the core/vesel
    Returns:
    --------
    out: Materialflow object
        Material data after performing all removals
    waste: dictionary
        key: process name
        value: Materialflow object containing waste streams data
    """
    inmass = {}
    extracted_mass = {}
    waste = OrderedDict()
    forked_mat = OrderedDict()
    # out = OrderedDict()
    prcs = read_processes_from_input()
    mat_name_dot, paths = read_dot(dot_inp_file)
    for mname in prcs.keys():  # iterate over materials
        waste[mname] = {}
        forked_mat[mname] = []
        inmass[mname] = float(mat[mname].mass)
        print("Material mass before reprocessing %f g" % inmass[mname])
        if mname == 'fuel' and mat_name_dot == 'fuel':
            w = 'waste_'
            ctr = 0
            for path in paths:
                forked_mat[mname].append(copy.deepcopy(mat[mname]))
                print("Material mass %f" % mat[mname].mass)
                for p in path:
                    # Calculate fraction of the flow going to the process p
                    divisor = float(prcs[mname][p].mass_flowrate /
                                    prcs[mname]['core_outlet'].mass_flowrate)
                    print('Process %s, divisor=%f' % (p, divisor))
                    # Update materialflow byt multiplying it by flow fraction
                    forked_mat[mname][ctr] = \
                        divisor * copy.deepcopy(forked_mat[mname][ctr])
                    waste[mname][w+p] = \
                        prcs[mname][p].rem_elements(forked_mat[mname][ctr])
                ctr += 1
            # Sum all forked material objects together
            mat[mname] = forked_mat[mname][0]  # initilize correct obj instance
            for idx in range(1, len(forked_mat[mname])):
                mat[mname] += forked_mat[mname][idx]
            print('1 Forked material mass %f' % (forked_mat[mname][0].mass))
            print('2 Forked material mass %f' % (forked_mat[mname][1].mass))
            print(waste[mname].keys())
            print('\nMass balance %f g = %f + %f + %f + %f + %f + %f' %
                  (inmass[mname],
                   mat[mname].mass,
                   waste[mname]['waste_sparger'].mass,
                   waste[mname]['waste_entrainment_separator'].mass,
                   waste[mname]['waste_nickel_filter'].mass,
                   waste[mname]['waste_bypass'].mass,
                   waste[mname]['waste_liquid_metal'].mass))
            # del inflowA, outflowC
        # Bootstrap for many materials
        if mname == 'ctrlPois':
            waste[mname]['removal_tb_dy'] = \
                prcs[mname]['removal_tb_dy'].rem_elements(mat[mname])
        extracted_mass[mname] = inmass[mname] - float(mat[mname].mass)
    del prcs, inmass, mname, forked_mat, mat_name_dot, paths, divisor
    return waste, extracted_mass


def refill(mat, extracted_mass, waste_dict):
    """ Applies reprocessing scheme to selected material

    Parameters:
    -----------
    mat: dictionary
        key: Material name
        value: Materialflow object`
        Material data right after reprocessing plant
    extracted_mass: dictionary
        key: Material name
        value: float
        Mass removed as waste in reprocessing function for each material
    Returns:
    --------
    refill_stream: Materialflow object
        Material data after refill
    """
    print('Fuel before refill ^^^', mat['fuel'].print_attr())
    feeds = read_feeds_from_input()
    refill_mat = OrderedDict()
    # out = OrderedDict()
    # print (feeds['fuel'])
    for mn, v in feeds.items():  # iterate over materials
        refill_mat[mn] = {}
        # out[mn] = {}
        for feed_n, fval in feeds[mn].items():  # works with one feed only
            scale = extracted_mass[mn]/feeds[mn][feed_n].mass
            refill_mat[mn] = scale * feeds[mn][feed_n]
            waste_dict[mn]['feed_'+str(feed_n)] = refill_mat[mn]
        mat[mn] += refill_mat[mn]
    print('Refilled fresh fuel %f g' % refill_mat['fuel'].mass)
    print('Refilled fresh Gd %f g' % refill_mat['ctrlPois'].mass)
    print('Refill Material ^^^', refill_mat['fuel'].print_attr())
    print('Fuel after refill ^^^', mat['fuel'].print_attr())
    return waste_dict


def check_restart():
    if not restart_flag:
        try:
            os.remove(db_file)
            os.remove(iter_matfile)
            os.remove(input_file)
        except OSError as e:
            print("Error while deleting file, ", e)


def run():
    """ Inititialize main run
    """
    # Print out input information
    print('Initiating Saltproc:\n'
          '\tRestart = ' + str(restart_flag) + '\n'
          # '\tNodes = ' + str(nodes) + '\n'
          '\tCores = ' + str(cores) + '\n'
          # '\tSteps = ' + str(steps) + '\n'
          # '\tBlue Waters = ' + str(bw) + '\n'
          '\tSerpent Path = ' + exec_path + '\n'
          '\tTemplate File Path = ' + template_file + '\n'
          '\tInput File Path = ' + input_file + '\n'
          # '\tMaterial File Path = ' + mat_file + '\n'
          # '\tOutput DB File Path = ' + db_file + '\n'
          )
    # Intializing objects
    serpent = Depcode(codename='SERPENT',
                      exec_path=exec_path,
                      template_fname=template_file,
                      input_fname=input_file,
                      output_fname='NONE',
                      iter_matfile=iter_matfile,
                      geo_file=geo_file,
                      npop=neutron_pop,
                      active_cycles=active_cycles,
                      inactive_cycles=inactive_cycles)
    simulation = Simulation(sim_name='Super test',
                            sim_depcode=serpent,
                            core_number=cores,
                            node_number=nodes,
                            h5_file=db_file,
                            compression=compression_prop,
                            iter_matfile=iter_matfile,
                            timesteps=steps)

    # Check: Restarting previous simulation or starting new?
    check_restart()
    # Run sequence
    # Start sequence
    for dts in range(steps):
        print("\n\n\nStep #%i has been started" % (dts+1))
        if dts == 0 and restart_flag is False:  # First step
            serpent.write_depcode_input(template_file, input_file)
            serpent.run_depcode(cores, nodes)
            # Read general simulation data which never changes
            simulation.store_run_init_info()
            # Parse and store data for initial state (beginning of dts
            mats = serpent.read_dep_comp(input_file, 0)  # 0)
            simulation.store_mat_data(mats, dts-1, 'before_reproc')
        # Finish of First step
        # Main sequence
        else:
            serpent.run_depcode(cores, nodes)
        mats = serpent.read_dep_comp(input_file, 1)
        simulation.store_mat_data(mats, dts, 'before_reproc')
        simulation.store_run_step_info()
        # Reprocessing here
        print("\nMass and volume of fuel before reproc %f g; %f cm3" %
              (mats['fuel'].mass,
               mats['fuel'].vol))
        print("Mass and volume of ctrlPois before reproc %f g; %f cm3" %
              (mats['ctrlPois'].mass,
               mats['ctrlPois'].vol))
        waste_st, rem_mass = reprocessing(mats)
        print("\nMass and volume of fuel after reproc %f g; %f cm3" %
              (mats['fuel'].mass,
               mats['fuel'].vol))
        print("Mass and volume of ctrlPois after reproc %f g; %f cm3" %
              (mats['ctrlPois'].mass,
               mats['ctrlPois'].vol))
        refill(mats, rem_mass, waste_st)
        print("\nMass and volume of fuel after REFILL %f g; %f cm3" %
              (mats['fuel'].mass,
               mats['fuel'].vol))
        print("Mass and volume of ctrlPois after REFILL %f g; %f cm3" %
              (mats['ctrlPois'].mass,
               mats['ctrlPois'].vol))
        print("Removed mass [g]:", rem_mass)
        # Store in DB after reprocessing and refill (right before next depl)
        simulation.store_after_repr(mats, waste_st, dts)
        serpent.write_mat_file(mats, iter_matfile, dts)
        del mats, waste_st, rem_mass
        gc.collect()
        # Switch to another geometry?
        if adjust_geo and simulation.read_k_eds_delta(dts, restart_flag):
            simulation.switch_to_next_geometry()

        print("\nSimulation succeeded.\n")
