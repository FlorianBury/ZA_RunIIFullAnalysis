#!/usr/bin/env python

import re
import math
import glob
import csv
import os
import sys
import pprint
import logging
import copy
import pickle
#import psutil
from functools import reduce
import operator
import itertools
import matplotlib.pyplot as plt
if plt.rcParams['backend'] == 'TkAgg':
    raise ImportError("Change matplotlib backend to 'Agg' in ~/.config/matplotlib/matplotlibrc")

import argparse
import numpy as np
import pandas as pd

from sklearn import preprocessing
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from sklearn.preprocessing import OneHotEncoder

# Personal files #
    
def get_options():
    """
    Parse and return the arguments provided by the user.
    """
    parser = argparse.ArgumentParser(description='MoMEMtaNeuralNet : A tool to regress the Matrix Element Method with a Neural Network')

    # Scan, deploy and restore arguments #
    a = parser.add_argument_group('Scan, deploy and restore arguments')
    a.add_argument('-s','--scan', action='store', required=False, type=str, default='',
        help='Name of the scan to be used (modify scan parameters in NeuralNet.py)')
    a.add_argument('-task','--task', action='store', required=False, type=str, default='',
        help='Name of dict to be used for scan (Used by function itself when submitting jobs or DEBUG)')
    a.add_argument('--generator', action='store_true', required=False, default=False, 
        help='Wether to use a generator for the neural network')
    a.add_argument('--resume', action='store_true', required=False, default=False,
        help='Wether to resume the training of a given model (path in parameters.py)')

    # Splitting and submitting jobs arguments #
    b = parser.add_argument_group('Splitting and submitting jobs arguments')
    b.add_argument('-split','--split', action='store', required=False, type=int, default=0,
        help='Number of parameter sets per jobs to be used for splitted training for slurm submission (if -1, will create a single subdict)')
    b.add_argument('-submit','--submit', action='store', required=False, default='', type=str,
        help='Wether to submit on slurm and name for the save (must have specified --split)')
    b.add_argument('-resubmit','--resubmit', action='store', required=False, default='', type=str,
        help='Wether to resubmit failed jobs given a specific path containing the jobs that succeded')
    b.add_argument('-debug','--debug', action='store_true', required=False, default=False,
        help='Debug mode of the slurm submission, does everything except submit the jobs')

    # Analyzing or producing outputs for given model (csv or zip file) #
    c = parser.add_argument_group('Analyzing or producing outputs for given model (csv or zip file)')
    c.add_argument('-r','--report', action='store', required=False, type=str, default='',
        help='Name of the csv file for the reporting (without .csv)')
    c.add_argument('-m','--model', action='store', required=False, type=str, default='',                                                                                                          
        help='Loads the provided model name (without .zip and type, it will find them)') 
    c.add_argument('--test', action='store_true', required=False, default=False,
        help='Applies the provided model (do not forget -o) on the test set and output the tree') 
    c.add_argument('-o','--output', action='store', required=False, nargs='+', type=str, default=[], 
        help='Applies the provided model (do not forget -o) on the list of keys from sampleList.py (separated by spaces)') 

    # Concatenating csv files arguments #
    d = parser.add_argument_group('Concatenating csv files arguments')
    d.add_argument('-csv','--csv', action='store', required=False, type=str, default='',
        help='Wether to concatenate the csv files from different slurm jobs into a main one, \
              please provide the path to the csv files')

    # Concatenating csv files arguments #
    e = parser.add_argument_group('Physics arguments')
    e.add_argument('--resolved', action='store_true', required=False, default=False,
       help='Resolved topology')
    e.add_argument('--boosted', action='store_true', required=False, default=False,
       help='Boosted topology')


    # Additional arguments #
    f = parser.add_argument_group('Additional arguments')
    f.add_argument('-v','--verbose', action='store_true', required=False, default=False,
        help='Show DEGUG logging')
    f.add_argument('--GPU', action='store_true', required=False, default=False,
        help='GPU requires to execute some commandes before')

    opt = parser.parse_args()

    if opt.split!=0 or opt.submit!='':
        if opt.scan!='' or opt.report!='':
            logging.critical('These parameters cannot be used together')  
            sys.exit(1)
    if opt.submit!='': # Need --output or --split arguments
        if opt.split==0 and len(opt.output)==0:
            logging.warning('In case of learning you forgot to specify --split')
            sys.exit(1)
    if opt.split!=0 and (opt.report!='' or opt.output!='' or opt.csv!='' or opt.scan!=''):
        logging.warning('Since you have specified a split, all the other arguments will be skipped')
    if opt.csv!='' and (opt.report!='' or opt.output!='' or opt.scan!=''):
        logging.warning('Since you have specified a csv concatenation, all the other arguments will be skipped')
    if opt.report!='' and (opt.output!='' or opt.scan!=''):
        logging.warning('Since you have specified a scan report, all the other arguments will be skipped')
    if (opt.test or len(opt.output)!=0) and opt.output == '': 
        logging.critical('You must specify the model with --output')
        sys.exit(1)
    if opt.generator:
        logging.info("Will use the generator")
    if opt.resume:
        logging.info("Will resume the training of the model")

    return opt

def main():
    #############################################################################################
    # Preparation #
    #############################################################################################
    # Get options from user #
    logging.basicConfig(level=logging.DEBUG,format='%(asctime)s - %(levelname)s - %(message)s',datefmt='%m/%d/%Y %H:%M:%S')
    opt = get_options()
    # Verbose logging #
    if not opt.verbose:
        logging.getLogger().setLevel(logging.INFO)


    # Private modules containing Pyroot #
    from NeuralNet import HyperModel
    from import_tree import LoopOverTrees
    from produce_output import ProduceOutput
    from make_scaler import MakeScaler
    from submit_on_slurm import submit_on_slurm
    from generate_mask import GenerateMask
    from split_training import DictSplit
    from concatenate_csv import ConcatenateCSV
    from sampleList import samples_dict, samples_path
    from threadGPU import utilizationGPU
    import parameters

    # Needed because PyROOT messes with argparse

    logging.info("="*94)
    logging.info("  _____   _    __  __            _     _            _                          _             ")
    logging.info(" |__  /  / \  |  \/  | __ _  ___| |__ (_)_ __   ___| |    ___  __ _ _ __ _ __ (_)_ __   __ _ ")
    logging.info("   / /  / _ \ | |\/| |/ _` |/ __| '_ \| | '_ \ / _ \ |   / _ \/ _` | '__| '_ \| | '_ \ / _` |")
    logging.info("  / /_ / ___ \| |  | | (_| | (__| | | | | | | |  __/ |__|  __/ (_| | |  | | | | | | | | (_| |")
    logging.info(" /____/_/   \_\_|  |_|\__,_|\___|_| |_|_|_| |_|\___|_____\___|\__,_|_|  |_| |_|_|_| |_|\__, |")
    logging.info("                                                                                       |___/ ")
    logging.info("="*94)

    # Make path model #
    path_model = os.path.join(parameters.main_path,'model')
    if not os.path.exists(path_model):
        os.mkdir(path_model)

    #############################################################################################
    # Splitting into sub-dicts and slurm submission #
    #############################################################################################
    if opt.submit != '':
        if opt.split != 0:
            DictSplit(opt.split,opt.submit,opt.resubmit)
            logging.info('Splitting jobs done')
        
        # Arguments to send #
        args = ' ' # Do not forget the spaces after each arg!
        if opt.resolved:            args += ' --resolved '
        if opt.boosted:             args += ' --boosted '
        if opt.generator:           args += ' --generator '
        if opt.GPU:                 args += ' --GPU '
        if opt.resume:              args += ' --resume '
        if opt.model!='':           args += ' --model '+opt.model+' '
        if len(opt.output)!=0:      args += ' --output '+ ' '.join(opt.output)+' '

        if opt.submit!='':
            logging.info('Submitting jobs with args "%s"'%args)
            if opt.resubmit:
                submit_on_slurm(name=opt.submit+'_resubmit',debug=opt.debug,args=args)
            else:
                submit_on_slurm(name=opt.submit,debug=opt.debug,args=args)
        sys.exit()

    #############################################################################################
    # CSV concatenation #
    #############################################################################################
    if opt.csv!='':
        logging.info('Concatenating csv files from : %s'%(opt.csv))
        dict_csv = ConcatenateCSV(opt.csv)
        dict_csv.Concatenate()
        dict_csv.WriteToFile()

        sys.exit()

    #############################################################################################
    # Reporting given scan in csv file #
    #############################################################################################
    if opt.report != '':
        instance = HyperModel(opt.report)
        instance.HyperReport(parameters.eval_criterion)

        sys.exit()

    #############################################################################################
    # Output of given files from given model #
    #############################################################################################
    if opt.model != '' and len(opt.output) != 0:
        # Create directory #
        path_output = os.path.join(parameters.path_out,opt.model)
        if not os.path.exists(path_output):
            os.mkdir(path_output)

        # Instantiate #
        inst_out = ProduceOutput(model=os.path.join(parameters.path_model,opt.model),generator=opt.generator)
        # Loop over output keys #
        for key in opt.output:
            # Create subdir #
            path_output_sub = os.path.join(path_output,key+'_output')
            if not os.path.exists(path_output_sub):
                os.mkdir(path_output_sub)
            try:
                inst_out.OutputNewData(input_dir=samples_path,list_sample=samples_dict[key],path_output=path_output_sub)
            except Exception as e:
                logging.critical('Could not process key "%s" due to "%s"'%(key,e))
        sys.exit()
    #############################################################################################
    # Data Input and preprocessing #
    #############################################################################################
    # Memory Usage #
    #pid = psutil.Process(os.getpid())
    logging.info('Current pid : %d'%os.getpid())

    # Input path #
    logging.info('Starting tree importation')

    # Import variables from parameters.py
    variables = parameters.inputs+parameters.outputs+parameters.other_variables
    list_inputs  = parameters.inputs
    list_outputs = parameters.outputs

    topologies = []
    if opt.resolved:
        topologies.append("resolved")
    if opt.boosted:
        topologies.append("boosted")
    if len(topologies) == 0:
        raise RuntimeError("No correct topology has been specified")


    if not opt.generator:
        # Import arrays #
        logging.info('Background samples')
        DY_list = []
        TT_list  = []
        ZA_list  = []
        for topo in topologies:
            DY_list.extend(samples_dict['%s_DY'%topo])
            TT_list.extend(samples_dict['%s_TT'%topo])
            ZA_list.extend(samples_dict['%s_ZA'%topo])
            
        data_DY = LoopOverTrees(input_dir                 = samples_path,
                                variables                 = variables,
                                weight                    = parameters.weights,
                                list_sample               = DY_list,
                                cut                       = parameters.cut,
                                xsec_json                 = parameters.xsec,
                                event_weight_sum_json     = parameters.event_weight_sum,
                                tag                       = 'DY')
        logging.info('DY sample size : {}'.format(data_DY.shape[0]))
        data_TT = LoopOverTrees(input_dir                 = samples_path,
                                variables                 = variables,
                                weight                    = parameters.weights,
                                list_sample               = TT_list,
                                cut                       = parameters.cut,
                                xsec_json                 = parameters.xsec,
                                event_weight_sum_json     = parameters.event_weight_sum,
                                tag                       = 'TT')
        logging.info('TT sample size : {}'.format(data_TT.shape[0]))
        data_ZA = LoopOverTrees(input_dir                   = samples_path,
                                variables                   = variables,
                                weight                      = parameters.weights,
                                list_sample                 = ZA_list,
                                cut                         = parameters.cut,
                                xsec_json                   = parameters.xsec,
                                event_weight_sum_json       = parameters.event_weight_sum,
                                tag                         = 'ZA')
        logging.info('Signal sample size : {}'.format(data_ZA.shape[0]))

        list_inputs  = [var.replace('$','') for var in parameters.inputs]
        list_outputs = [var.replace('$','') for var in parameters.outputs]

        #logging.info('Current memory usage : %0.3f GB'%(pid.memory_info().rss/(1024**3)))

        # Modify MA and MH for background #
        mass_prop_ZA = [(x, len(list(y))) for x, y in itertools.groupby(sorted(data_ZA[["mH","mA"]].values.tolist()))]
        mass_prop_DY = [(x,math.ceil(y/data_ZA.shape[0]*data_DY.shape[0])) for x,y in mass_prop_ZA]
        mass_prop_TT = [(x,math.ceil(y/data_ZA.shape[0]*data_TT.shape[0])) for x,y in mass_prop_ZA]
            # array of [(mH,mA), proportions]
        mass_DY = np.array(reduce(operator.concat, [[m]*n for (m,n) in mass_prop_DY]))
        mass_TT = np.array(reduce(operator.concat, [[m]*n for (m,n) in mass_prop_TT]))
        np.random.shuffle(mass_DY) # Shuffle so that each background event has random masses
        np.random.shuffle(mass_TT) # Shuffle so that each background event has random masses
        df_masses_DY = pd.DataFrame(mass_DY,columns=["mH","mA"]) 
        df_masses_TT = pd.DataFrame(mass_TT,columns=["mH","mA"]) 
        df_masses_DY = df_masses_DY[:data_DY.shape[0] ]# Might have slightly more entries due to numerical instabilities in props
        df_masses_TT = df_masses_TT[:data_TT.shape[0] ]# Might have slightly more entries due to numerical instabilities in props
        data_DY[["mH","mA"]] = df_masses_DY
        data_TT[["mH","mA"]] = df_masses_TT


        # Check the proportions #
        logging.debug("Check on the masses proportions")
        tot_DY = 0
        tot_TT = 0
        for masses, prop_in_ZA in mass_prop_ZA:
            prop_in_DY = data_DY[(data_DY["mH"]==masses[0]) & (data_DY["mA"]==masses[1])].shape[0]
            prop_in_TT = data_TT[(data_TT["mH"]==masses[0]) & (data_TT["mA"]==masses[1])].shape[0]
            logging.debug("... Mass point (MH = %d, MA = %d)\t: N signal = %d (%0.2f%%),\tN DY = %d (%0.2f%%)\tN TT = %d (%0.2f%%)"
                         %(masses[0],masses[1],prop_in_ZA,prop_in_ZA/data_ZA.shape[0]*100,prop_in_DY,prop_in_DY/data_DY.shape[0]*100,prop_in_TT,prop_in_TT/data_TT.shape[0]*100))
            tot_DY += prop_in_DY
            tot_TT += prop_in_TT
        assert tot_DY == data_DY.shape[0]
        assert tot_TT == data_TT.shape[0]

        # Weight equalization #
        if parameters.weights is not None:
            weight_DY = data_DY["event_weight"]
            weight_TT = data_TT["event_weight"]
            # Use mass prop weights so that eahc mass point has same importance #
            weight_ZA = np.zeros(data_ZA.shape[0])
            for m,p in mass_prop_ZA:    
                idx = list(data_ZA[(data_ZA["mH"]==m[0]) & (data_ZA["mA"]==m[1])].index)
                weight_ZA[idx] = 1./p
            # We need the different types to have the same sumf of weight to equalize training
            # Very small weights produce very low loss function, needs to add multiplicating factor
            weight_DY = weight_DY/np.sum(weight_DY)*1e5
            weight_TT = weight_TT/np.sum(weight_TT)*1e5
            weight_ZA = weight_ZA/np.sum(weight_ZA)*1e5
        else:
            weight_DY = np.ones(data_DY.shape[0])
            weight_TT = np.ones(data_TT.shape[0])
            weight_ZA = np.ones(data_ZA.shape[0])

        # Check sum of weight #
        if np.sum(weight_ZA) != np.sum(weight_TT) or np.sum(weight_ZA) != np.sum(weight_DY) or np.sum(weight_TT) != np.sum(weight_DY):
            logging.warning ('Sum of weights different between the samples')
            logging.warning('\tDY : '+str(np.sum(weight_DY)))
            logging.warning('\tTT : '+str(np.sum(weight_TT)))
            logging.warning('\tZA : '+str(np.sum(weight_ZA)))

        data_DY['learning_weights'] = pd.Series(weight_DY)
        data_TT['learning_weights'] = pd.Series(weight_TT)
        data_ZA['learning_weights'] = pd.Series(weight_ZA)
        #logging.info('Current memory usage : %0.3f GB'%(pid.memory_info().rss/(1024**3)))

        # Data splitting #
        mask_DY = GenerateMask(data_DY.shape[0],parameters.suffix+'_DY')
        mask_TT = GenerateMask(data_TT.shape[0],parameters.suffix+'_TT')
        mask_ZA = GenerateMask(data_ZA.shape[0],parameters.suffix+'_ZA')
           # Needs to keep the same testing set for the evaluation of model that was selected earlier
        try:
            train_DY = data_DY[mask_DY==True]
            train_TT = data_TT[mask_TT==True]
            train_ZA = data_ZA[mask_ZA==True]
            test_DY = data_DY[mask_DY==False]
            test_TT = data_TT[mask_TT==False]
            test_ZA = data_ZA[mask_ZA==False]
        except ValueError:
            logging.critical("Problem with the mask you imported, has the data changed since it was generated ?")
            raise ValueError
            
        #logging.info('Current memory usage : %0.3f GB'%(pid.memory_info().rss/(1024**3)))
        del data_TT , data_DY, data_ZA

        
        train_all = pd.concat([train_DY,train_TT,train_ZA],copy=True).reset_index(drop=True)
        test_all = pd.concat([test_DY,test_TT,test_ZA],copy=True).reset_index(drop=True)
        del train_TT, train_DY, train_ZA, test_TT, test_DY, test_ZA
        #logging.info('Current memory usage : %0.3f GB'%(pid.memory_info().rss/(1024**3)))

        # Randomize order, we don't want only one type per batch #
        random_train = np.arange(0,train_all.shape[0]) # needed to randomize x,y and w in same fashion
        np.random.shuffle(random_train) # Not need for testing
        train_all = train_all.iloc[random_train]
          
        # Add target #
        label_encoder = LabelEncoder()
        onehot_encoder = OneHotEncoder(sparse=False)
        label_encoder.fit(train_all['tag'])
        # From strings to labels #
        train_integers = label_encoder.transform(train_all['tag']).reshape(-1, 1)
        test_integers = label_encoder.transform(test_all['tag']).reshape(-1, 1)
        # From labels to strings #
        train_onehot = onehot_encoder.fit_transform(train_integers)
        test_onehot = onehot_encoder.fit_transform(test_integers)
        # From arrays to pd DF #
        train_cat = pd.DataFrame(train_onehot,columns=label_encoder.classes_,index=train_all.index)
        test_cat = pd.DataFrame(test_onehot,columns=label_encoder.classes_,index=test_all.index)
        # Add to full #
        train_all = pd.concat([train_all,train_cat],axis=1)
        test_all = pd.concat([test_all,test_cat],axis=1)

        # Preprocessing #
        # The purpose is to create a scaler object and save it
        # The preprocessing will be implemented in the network with a custom layer
        if opt.scan!='': # If we don't scan we don't need to scale the data
            MakeScaler(train_all,list_inputs) 
     
        logging.info("Sample size seen by network : %d"%train_all.shape[0])
        logging.info("Sample size for the output  : %d"%test_all.shape[0])
        #logging.info('Current memory usage : %0.3f GB'%(pid.memory_info().rss/(1024**3)))
    else:
        logging.info('No samples have been imported since you asked for a generator')
        train_all = pd.DataFrame()
        test_all = pd.DataFrame()
        MakeScaler(generator=True, list_inputs=list_inputs)

    #############################################################################################
    # DNN #
    #############################################################################################
    if opt.GPU:
        # Start the GPU monitoring thread #
        thread = utilizationGPU(print_time = 900,
                                print_current = False,
                                time_step=0.01)
        thread.start()

    if opt.scan != '':
        instance = HyperModel(opt.scan)
        instance.HyperScan(data=train_all,
                           list_inputs=list_inputs,
                           list_outputs=list_outputs,
                           task=opt.task,
                           generator=opt.generator,
                           resume=opt.resume)
        instance.HyperDeploy(best='eval_error')

    if opt.GPU:
        # Closing monitor thread #
        thread.stopLoop()
        thread.join()
        
    if opt.model!='': 
        # Make path #
        output_name = "test" 
        path_output = os.path.join(parameters.path_out,opt.model,output_name)
        if not os.path.exists(path_output):
            os.makedirs(path_output)

        # Instance of output class #
        inst_out = ProduceOutput(model=os.path.join(parameters.main_path,'model',opt.model),
                                 generator=opt.generator,
                                 list_inputs=list_inputs)

        # Use it on test samples #
        if opt.test:
            logging.info('  Processing test output sample  '.center(80,'*'))
            inst_out.OutputFromTraining(data=test_all,path_output=path_output)
            logging.info('')
             
   
if __name__ == "__main__":
    main()
