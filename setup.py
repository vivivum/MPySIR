###############################################################
#  MPySIR: MPI python script for SIR
#
#  CALL:    mpirun -n 10 python setup.py // mpirun --use-hwthread-cpus -n 128 python setup.py
##############################################################

"""
This script runs the SIR inversion in parallel using MPI.

SIRMODE:
====================
1.- 'perPixel': Each pixel is inverted independently
2.- 'continue': The inversion is continued from a previous one

# Not tested yet:
3.- 'gammaV': The inclination is inferred form the Stokes V profile
4.- 'addFullProfile': The synthetic profiles are in another grid

"""

# ================================================= OS
import os
# Forcing that each process only uses one thread:
os.environ['OPENBLAS_NUM_THREADS'] = '1'

# ================================================= TIME
import time; start_time = time.time()

# ================================================= LIBRARIES
import numpy as np
from mpi4py import MPI
import sirutils
from sirutils import pprint
import sirtools
import sys
import datetime

# ================================================= MPI INIT - CLEAN
comm = MPI.COMM_WORLD
widthT = 1
if comm.rank == 0:
    (widthT, heightT) = sirutils.getTerminalSize()
    print('-'*widthT)
    print('Running on %d cores' % comm.size)
    print('-'*widthT)
    sirutils.total_cores()
    from clean import clean; clean()
comm.Barrier()


# ================================================= PARAMETERS

# Retrieve all parameters from the config file:
from config import *

x = None
wavrange = None

# Updates malla.grid and sir.trol if master:
if comm.rank == 0:
    
    # Check if the files exists (if not, it will be copied inside the invDefault folder):
    Linesfile = sirutils.checkParamsfile(Linesfile)
    Abundancefile = sirutils.checkParamsfile(Abundancefile)
    sirfile = sirutils.checkParamsfile(sirfile)

    # For the reference wavelength we get it from the LINEAS file:
    lambdaRef = sirutils.getLambdaRef(dictLines,Linesfile)

    # Load wavelength (this is the same for all nodes):
    xlambda = sirutils.loadanyfile(wavefile)

    if wavrange is None:
        wavrange = range(len(xlambda))  # Wavelength range to be used in the inversion
    x = (xlambda[wavrange] -lambdaRef)*1e3  # Wavelength in mA

    # Modify the "malla.grid" file to change the wavelength range.
    sirutils.modify_malla(dictLines, x)
    
    # Modify the "sir.trol" file to change the inversion parameters.
    sirutils.modify_sirtrol(Nodes_temperature, Nodes_magneticfield, Nodes_LOSvelocity, Nodes_gamma, Nodes_phi, 
                            Invert_macroturbulence, Linesfile, Abundancefile,mu_obs, Nodes_microturbulence, weightStokes)
    
    # SIR will only allow to use a number of nodes which is divisor of ntau-1, so we need to modify the numnber of nodes:
    nodes_allowed = sirutils.calculate_nodes()
    print('[INFO] Nodes allowed = '+str(nodes_allowed))
    
    # Modify the micro and macro ONLY if starting from a previous model:
    if Initial_vmacro is not None:
        # Modify the initial model with the initial macro velocity:
        sirutils.modify_vmacro(Initial_vmacro)
    else:
        print('[INFO] Initial macroturbulence not modified')

    if Initial_micro is not None:
        # Modify the initial model with the initial micro velocity:
        sirutils.modify_vmicro(Initial_micro)
    else:
        print('[INFO] Initial microturbulence not modified')

# Broadcast: x and wavrange:
comm.Barrier()
x = comm.bcast(x, root=0)
wavrange = comm.bcast(wavrange, root=0)


# ================================================= LOAD INPUT DATA
# Now only the master node reads the data and broadcasts it to the rest of the nodes:
if comm.rank == 0:

    image = sirutils.loadanyfile(inpufile)

    # We now swap the axes to [ny, nx, ns, nw]:
    pprint('[INFO] Before - Image shape: '+str(image.shape))
    from einops import rearrange
    image = rearrange(image, original_axis+'-> ny nx ns nw')
    pprint('[INFO] After - Image shape: '+str(image.shape))


    # If fov is not None, we extract a portion of the image:
    if fov is not None:
        xstart, ystart = int(fov_start.split(',')[0]), int(fov_start.split(',')[1])
        image = image[0+xstart:int(fov.split(',')[0])+xstart,0+ystart:int(fov.split(',')[1])+ystart,:,:]
    
    # If skip is not 1, we skip pixels:
    if skip != 1:
        image = image[::skip,::skip,:,:]
        print('[INFO] Skipping '+str(skip)+' pixels. New image shape: '+str(image.shape))

    # Data dimensions:
    height, width, nStokes, nLambdas = image.shape

    # Now we divide the image in portions and send them to the nodes. For that,
    # we can flatten the X&Y dimensions and divide them with array_split:
    totalpixels = height*width
    print('[INFO] Total pixels: '+str(totalpixels))
    listofpixels = np.arange(totalpixels)
    listofparts = np.array_split(listofpixels, comm.size)
    
    # We need to flatten the image to send it to the nodes, by
    # moving the X&Y dimensions to the first axis and then flattening:
    image = image.reshape((totalpixels, nStokes, nLambdas))    
    
    # Print the list of parts for each node:
    for nodei in range(comm.size):
        if verbose:
            print('[INFO] Node '+str(nodei)+' will receive: from pixel '+str(listofparts[nodei][0])+' to '+str(listofparts[nodei][-1]))

    # Divide the image in small portions and broadcast them to the nodes:
    for nodei in range(1, comm.size):
        myrange = listofparts[nodei]
        comm.send(image[myrange,:,:], dest = nodei, tag = 100)

    # The master node keeps the first portion:
    myrange = listofparts[0]
    myPart = np.copy(image[myrange,:,:])
    del image # We delete the original image to save memory.
    if verbose: print('Node 0 received data -> '+str(myPart.shape))

    
    # ================================================= LOAD CONTINUE MODEL
    if sirmode == 'continue' and continuemodel is not None:
        print('[INFO] Inversion from previous model: '+continuemodel)

        # We load the model:
        init_model = np.load(continuemodel)
        # If it was created with inv2model routine it should have the axes: [ny, nx, ntau, npar]
        
        # We now also flatten the model to send it to the nodes, by
        # flattening, as model is already in the correct axes:
        init_model = init_model.reshape((init_model.shape[0]*init_model.shape[1], init_model.shape[2], init_model.shape[3]))
        
        # We now broadcast the model to the rest of the nodes like the data:
        for nodei in range(1, comm.size):
            myrange = listofparts[nodei]
            comm.send(init_model[myrange,:,:], dest = nodei, tag = 200)
            
        # The master node keeps the first portion:
        myrange = listofparts[0]
        myInit_model = np.copy(init_model[myrange,:,:])
        if verbose: print('Node 0 received new init model -> '+str(myInit_model.shape))
        del init_model # We delete the original image to save memory.
        
    
if comm.rank != 0:
    
    # ================================================= LOAD INPUT DATA
    # The rest of the nodes receive their portion:
    myPart = comm.recv(source = 0, tag = 100)
    if verbose:
        print('Node '+str(comm.rank)+' received data -> '+str(myPart.shape),flush=True)

    # ================================================= LOAD CONTINUE MODEL
    if sirmode == 'continue' and continuemodel is not None:
        myInit_model = comm.recv(source = 0, tag = 200)
        if verbose:
            print('Node '+str(comm.rank)+' received new init model -> '+str(myInit_model.shape),flush=True)


comm.Barrier()
pprint('==> Data loaded ..... {0:2.3f} s'.format(time.time() - start_time))
pprint('-'*widthT)


# ================================================= COPY FILES
# We prepare the directory with all necesary
os.system('cp -R invDefault node'+str(comm.rank))
# if verbose: print('Node '+str(comm.rank)+' prepared.',flush=True)
comm.Barrier()
time.sleep(0.1) # We wait a bit to avoid problems with the prints


# ================================================= INVERSION
curr = os.getcwd()  # Current path
os.chdir(curr+'/node'+str(comm.rank))  # enter to each node_folder

comm.Barrier()
pprint('==> Ready to start! ..... {0:2.3f} s'.format(time.time() - start_time))



# We execute SIR in the following way:
sirfile = './'+sirfile


totalPixel = myPart.shape[0]
if comm.rank == 0: print(f'\r... {0:4.2f} % ...'.format(0.0), end='', flush=True)


# We invert only one pixel if test1pixel is True:
if test1pixel:
    totalPixel = 1
    comm.Barrier()
    pprint('==> Testing 1 pixel ..... {0:2.3f} s'.format(time.time() - start_time))


# Ensure that the first column has a single line when writting the "data.per" file
if len(dictLines['atom'].split(',')) > 1:
    wperfilLine = dictLines['atom'].split(',')[0]
else:
    wperfilLine = dictLines['atom']


# If we are continuing a inversion from a previous model, we modify the initial model:
if sirmode == 'continue' and continuemodel is not None:
    # Use the file hsraB.mod as the baseline model for changing the initial model:
    [tau_init, model_init] = sirtools.lmodel12('hsraB.mod')


# Containers for the results:
resultadoSir = []


# Start the inversion:
inversion_time = time.time()  # Record the start time

# ================================================= INVERSION
# Start the inversion:
for currentPixel in range(0,totalPixel):
    mapa = myPart[currentPixel,:,:]
    stokes = [mapa[0,wavrange],mapa[1,wavrange],mapa[2,wavrange],mapa[3,wavrange]]
    sirtools.wperfil('data.per',wperfilLine,x,stokes)
    
    if sirmode == 'continue' and continuemodel is not None:
        # We write the initial model as hsraB.mod which is the default name for the initial model in SIR [ny, nx, ntau, npar]
        init_pixel = myInit_model[currentPixel,:,:]
        sirutils.write_continue_model(tau_init, model_init, init_pixel, final_filename='hsraB.mod',apply_constraints=apply_constraints)
        
        # This can be done much easier by pushing the values into the init_pixel variable. But we keep it like this for now.
        if Initial_vmacro is not None:
            # Modify the initial model with the initial macro velocity:
            sirutils.modify_vmacro(Initial_vmacro, filename_base='hsraB.mod', filename_final='hsraB.mod', verbose=False)
        if Initial_micro is not None:
            # Modify the initial model with the initial micro velocity:
            sirutils.modify_vmicro(Initial_micro, filename_base='hsraB.mod', filename_final='hsraB.mod', verbose=False)

    # +++++++++ Run SIR +++++++++
    sirutils.sirexe(comm.rank,sirfile, resultadoSir, sirmode, chi2map)

    if test1pixel:
        sirutils.plotper()  # Plots the profiles if we are testing 1 pixel
        sirutils.plotmfit() # Plots the model if we are testing 1 pixel
    
    # Print the percentage of the inversion:
    percentage = float(currentPixel)/float(totalPixel)*100.
    elapsed_time = time.time() - inversion_time
    remaining_time = elapsed_time * (totalPixel - currentPixel) / (currentPixel + 1)
    remaining_time = datetime.timedelta(seconds=remaining_time)  # Convert remaining_time to timedelta format
    remaining_time_str = str(remaining_time).split(".")[0]  # Exclude milliseconds from remaining time string

    if comm.rank == 0:
        print('\r... {0:4.2f}% -> {1} ...'.format(percentage, remaining_time_str), end='', flush=True)


comm.Barrier()
pprint('\n')
pprint('==> Inversion finished. Now gathering the results ..... {0:2.3f} s'.format(time.time() - start_time))
pprint('-'*widthT)







# ================================================= SAVING RESULTS
# The results are transformed to a numpy array to be able to save them using MPI:
resultadoSir = np.array(resultadoSir, dtype=object)

if test1pixel:
    # Exit the program if we are testing 1 pixel
    sys.exit()

# Gather the results in the master node:
if comm.rank != 0:
    # We send the results to the master node:
	comm.send(resultadoSir, dest = 0 , tag = 0)
else:
    # We receive the results from the rest of the nodes:
    finalSir = []
    finalSir.append(resultadoSir)
    for nodei in range(1, comm.size):
        vari = comm.recv(source = nodei, tag = 0)
        finalSir.append(vari)
    os.chdir(curr)

    # Now that we have all the results for all the pixels, we can concatenate them
    # and reshape them to the original shape of the input data:
    finalSir = np.concatenate(finalSir, axis=0)
    finalSir = finalSir.reshape((height, int(finalSir.shape[0]/height),finalSir.shape[1],finalSir.shape[2]))


    # We now split the results in the different variables:
    npar = 12
    if chi2map is False: npar = 11
    sirutils.create_modelmap(finalSir, outputfile, npar)
    sirutils.create_profilemap(finalSir, outputfile)



comm.Barrier()
pprint('==> MPySIR <==')

# Print the total time in the format HH:MM:SS (hours, minutes, seconds):
total_time = time.time() - start_time
if comm.rank == 0:
    print('Total time: '+str(datetime.timedelta(seconds=total_time)))
    print('Output file: '+outputfile)
pprint('-'*widthT)


# We clean the directory with all the temporary files, except when we are testing the code:
if comm.rank == 0 and not test1pixel:
    clean()
    
