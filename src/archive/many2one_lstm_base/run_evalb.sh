#!/bin/bash

step=29128

evalb_path='/share/data/speech/Data/ttran/parser_misc/EVALB/evalb'
prm_file='/share/data/speech/Data/ttran/parser_misc/EVALB/seq2seq.prm'
model_dir=/home-nfs/ttran/transitory/speech-nlp/venv_projects/seq2seq_parser/tmp_results/model-lstm-base-0907

gold_file=${model_dir}/gold-step${step}.txt
br_file=${model_dir}/decoded-br-step${step}.txt
mx_file=${model_dir}/decoded-mx-step${step}.txt

$evalb_path -p $prm_file $gold_file $br_file > out_br.txt
$evalb_path -p $prm_file $gold_file $mx_file > out_mx.txt
