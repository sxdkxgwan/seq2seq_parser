#!/bin/bash

DATA_DIR=/tmp/ttran/swbd_data

if [ ! -e ${DATA_DIR} ]
then
    mkdir -p ${DATA_DIR}
    cp /share/data/speech/Data/ttran/for_batch_jobs/swbd_data/* ${DATA_DIR}
fi

#source /home-nfs/ttran/transitory/speech-nlp/venv_projects/venv1/bin/activate
# environment with GPU support
source /home-nfs/ttran/transitory/speech-nlp/venv_projects/ven4/bin/activate
source /home-nfs/ttran/environ

cd /home-nfs/ttran/transitory/speech-nlp/venv_projects/seq2seq_parser/t2p_swbd_only
TRAIN_DIR="/home-nfs/ttran/transitory/speech-nlp/venv_projects/seq2seq_parser/tmp_results/model-swbd-0901"

# total sentences ~= 90,000 --> batch size 128 gives around 703 steps / epoch
LD_PRELOAD="/home-nfs/ttran/sw/opt/lib/libtcmalloc.so" python train_swbd_only.py \
    --data_dir ${DATA_DIR} \
    --train_dir ${TRAIN_DIR} \
    --attention \
    --batch_size=128 \
    --steps_per_checkpoint=500 \
    --max_steps=100000 >> output.train_swbd_only.0901.txt
