import matplotlib.pyplot as plt
import numpy as np
from tqdm import tqdm
from astropy.io import fits
"""
This module contains functions to fix bad pixels in the inversion results by finding a better model from
another pixel.
"""

# ========================= FINDBEST =========================
# The first one is the baseline
inversion_results = 'inv_degraded_100_000_n2e-3/finalSIR_cycle3_model.npy'
# inversion_results = 'inv_degraded_100_000_n2e-3/finalSIR_cycle1_model.npy'
outputname = '_fbest.npy'


# Observed profiles
directory = "/mn/stornext/d20/RoCS/carlosjd/projects/wSPRESOL/data/"
# stokes = "sunspot_jmb_sir_synth_100.npy"
# stokes_name = "sunspot_jmb_sir_synth_n2e-3.npy"
stokes_name = "sunspot_jmb_sir_synth_100_n2e-3.npy"
observed_stokes = np.load(directory+stokes_name)
print("Using observed profiles: ",directory+stokes_name)
observed_stokes = observed_stokes.transpose(0,1,2,3) # (x,y,lambda,stokes)
print('observed_stokes.shape = ',observed_stokes.shape)

# Number of pixels to fix:
npix = 4*4000#4*4000
# npix = 400
# npix = int(0.01*observed_stokes.shape[0]*observed_stokes.shape[1])
print('npix = ',npix)

# Load the inversion model as baseline
inversion_model = np.load(inversion_results)
stokes = np.load(inversion_results.replace('model', 'profiles'))

# Create a copy of the inversion and stokes where we will fix the worst pixels:
inversion_model_final = inversion_model.copy()
stokes_final = stokes.copy()

# Calculate the chi2 maps:
print("Calculate the initial chi2 map...")
chi2map = np.sum((observed_stokes[:,:,:,:]-stokes[:,:,:,:])**2.0,axis=(2,3))/observed_stokes.shape[3]


# Start fixing the worst pixels:
print("Fixing the worst pixels...")
chi2map_ordered = np.sort(chi2map.flatten())
for i in tqdm(range(npix)):
    # Find the pixel with the highest chi2:
    index = np.where(chi2map == chi2map_ordered[-(i+1)])
    # Take only one pixel:
    index = (index[0][0],index[1][0])
    
    # Calculate the new chi2map for this pixel:
    ichi2map = np.sum((observed_stokes[index[0],index[1],:,:]-stokes[:,:,:,:])**2.0,axis=(2,3))/observed_stokes.shape[3]
    
    # Find the pixel with the lowest chi2:
    index_min_chi2 = np.where(ichi2map == np.min(ichi2map))

    # Take only one pixel:
    index_min_chi2 = (index_min_chi2[0][0],index_min_chi2[1][0])
    
    # Is index the same as index_min_chi2?
    if index == index_min_chi2:
        print('Skipping: ',index,'no better pixel found.')
        continue
    
    print('Fixing: ',index,'<--',index_min_chi2, "(Dchi2: {0:1.1e})".format(chi2map[index[0],index[1]]-ichi2map[index_min_chi2[0],index_min_chi2[1]]))

    # Set the inversion results 
    inversion_model_final[index[0],index[1],:,:] = inversion_model[index_min_chi2[0],index_min_chi2[1],:,:]
    stokes_final[index[0],index[1],:,:] = stokes[index_min_chi2[0],index_min_chi2[1],:,:]

# DONE:
print("DONE!")

# Save the merged model:
np.save(inversion_results.replace('.npy',outputname), inversion_model_final.astype(np.float32))

# Save the merged stokes:
np.save(inversion_results.replace('model','profiles').replace('.npy',outputname), stokes_final.astype(np.float32))

# Notify using telegram that the inversion has finished.
import sirutils
sirutils.notify_telegram("[MPySIR][findbest.py] Fixing the worst pixels has finished for "+inversion_results+" and the observations "+stokes_name+".")
