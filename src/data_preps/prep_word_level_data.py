import os
import re
import sys
import glob
import pandas
from collections import defaultdict

import numpy as np
from tensorflow.python.platform import gfile
import tensorflow as tf
import cPickle as pickle

tf.app.flags.DEFINE_string("data_dir", "/s0/ttmt001/speech_parsing", \
        "directory of swbd data files")
tf.app.flags.DEFINE_string("output_dir", "/s0/ttmt001/speech_parsing/word_level", \
        "directory of output files")
tf.app.flags.DEFINE_integer("sp_scale", 10, "scaling of input buckets")
tf.app.flags.DEFINE_integer("avg_frame", 5, "number of frames to average over")
FLAGS = tf.app.flags.FLAGS

data_dir = FLAGS.data_dir
output_dir = FLAGS.output_dir

mfcc_dir = data_dir + '/swbd_mfcc'
time_dir = data_dir + '/swbd_trees'
pitch_dir = data_dir + '/swbd_pitch'
pitch_pov_dir = data_dir + '/swbd_pitch_pov'
fbank_dir = data_dir + '/swbd_fbank'

hop = 10.0 # in msec

# Regular expressions used to tokenize.
_WORD_SPLIT = re.compile(b"([.,!?\"':;)(])")
_DIGIT_RE = re.compile(br"\d")

# Special vocabulary symbols - we always put them at the start.
_PAD = b"_PAD"
_GO = b"_GO"
_EOS = b"_EOS"
_UNK = b"_UNK"
_UNF = b"_UNF"
_START_VOCAB = [_PAD, _GO, _EOS, _UNK, _UNF]
_PUNCT = ["'", "`",'"', ",", ".", "/", "?", "[", "]", "(", ")", "{", "}", ":",
";", "!"]

PAD_ID = 0
GO_ID = 1
EOS_ID = 2
UNK_ID = 3
UNF_ID = 4

# Use the following buckets: 
_buckets = [(10, 40), (25, 85), (40, 150)]

def basic_tokenizer(sentence):
  """Very basic tokenizer: split the sentence into a list of tokens."""
  words = []
  for space_separated_fragment in sentence.strip().split():
    words.extend(re.split(_WORD_SPLIT, space_separated_fragment))
  return [w for w in words if w]

def initialize_vocabulary(vocabulary_path):
    if gfile.Exists(vocabulary_path):
        rev_vocab = []
        with gfile.GFile(vocabulary_path, mode="rb") as f:
            rev_vocab.extend(f.readlines())
        rev_vocab = [line.strip() for line in rev_vocab]
        vocab = dict([(x, y) for (y, x) in enumerate(rev_vocab)])
        return vocab, rev_vocab
    else:
        raise ValueError("Vocabulary file %s not found.", vocabulary_path)

def sentence_to_token_ids(sentence, vocabulary, normalize_digits=True, lower=False):
    words = sentence.strip().split()
    if lower: words = [w.lower() for w in words]
    if not normalize_digits:
        return [vocabulary.get(w, UNK_ID) for w in words]
    # Normalize UNF and digits by 0 before looking words up in the vocabulary.
    for i, w in enumerate(words):
        if w[-1] == '-': words[i] = _UNF  # unfinished word normalization
    return [vocabulary.get(re.sub(_DIGIT_RE, b"0", w), UNK_ID) for w in words]

def summarize_mfcc(mfccs):
    start_indices = np.arange(0, len(mfccs), FLAGS.avg_frame)
    mfccs = np.array(mfccs)
    summarized = [np.mean(mfccs[offset:offset+FLAGS.avg_frame, :], axis=0) \
            for offset in start_indices]
    return summarized    

def make_array(frames):
    return np.array(frames).T

def convert_to_array(str_vector):
    str_vec = str_vector.replace('[','').replace(']','').replace(',','').split()
    num_list = []
    for x in str_vec:
        x = x.strip()
        if x != 'None': num_list.append(float(x))
        else: num_list.append(np.nan)
    return num_list

def has_bad_alignment(num_list):
    for i in num_list:
        if i < 0 or np.isnan(i): return True
    return False

def find_bad_alignment(num_list):
    bad_align = []
    for i in range(len(num_list)):
        if num_list[i] < 0 or np.isnan(num_list[i]): 
            bad_align.append(i)
    return bad_align

def has_bad_edge(num_list):
    start = num_list[0]
    end = num_list[-1]   
    if start<-1 or end<-1: return True
    if np.isnan(start) or np.isnan(end): return True
    return False

def prune_punct(num_list, tokens):
    new_list = []
    assert len(num_list)==len(tokens)
    for i in range(len(num_list)):
        if tokens[i] in _PUNCT: continue
        new_list.append(num_list[i])
    return new_list

def check_valid(num):
    if num < 0 or np.isnan(num): return False
    return True

def clean_up(stimes, etimes):
    if not check_valid(stimes[-1]):
        stimes[-1] = max(etimes[-1] - 0.05,0)
    
    if not check_valid(etimes[0]):
        etimes[0] = stimes[0] + 0.05
    
    for i in range(1,len(stimes)-1):
        this_st = stimes[i]
        prev_st = stimes[i-1]
        next_st = stimes[i+1]

        this_et = etimes[i]
        prev_et = etimes[i-1]
        next_et = etimes[i+1]   
   
        if not check_valid(this_st) and check_valid(prev_et):
            stimes[i] = prev_et

        if not check_valid(this_st) and check_valid(prev_st):
            stimes[i] = prev_st + 0.02

    for i in range(1,len(etimes)-1)[::-1]:
        this_st = stimes[i]
        prev_st = stimes[i-1]
        next_st = stimes[i+1]

        this_et = etimes[i]
        prev_et = etimes[i-1]
        next_et = etimes[i+1]   
        if not check_valid(this_et) and check_valid(next_st):
            etimes[i] = next_st

        if not check_valid(this_et) and check_valid(next_et):
            etimes[i] = next_et - 0.02

    return stimes, etimes

def get_stats(split):
    lengths = []
    wlens = []
    data_file = os.path.join(time_dir, split + '.data.csv')
    df = pandas.read_csv(data_file, sep='\t')
    sw_files = set(df.file_id.values)
    for sw in sw_files:
        this_dict = defaultdict(dict) 
        for speaker in ['A', 'B']:
            mfcc_file = os.path.join(mfcc_dir, sw + '-' + speaker + '.pickle')
            pitch_file = os.path.join(pitch_dir, sw + '-' + speaker + '.pickle')
            pitch_pov_file = os.path.join(pitch_pov_dir,sw+'-'+speaker+'.pickle')
            fbank_file = os.path.join(fbank_dir, sw + '-' +speaker+'.pickle')
            try:
                data_pitch = pickle.load(open(pitch_file))
            except: 
                print("No pitch file for ", sw, speaker)
                continue
            pitchs = data_pitch.values()[0]
            this_df = df[(df.file_id==sw)&(df.speaker==speaker)]
            for i, row in this_df.iterrows():
                tokens = row.sentence.strip().split()
                stimes = convert_to_array(row.start_times)
                etimes = convert_to_array(row.end_times)
                
                if len(stimes)==1: 
                    if (not check_valid(stimes[0])) or (not check_valid(etimes[0])):
                        continue
                 
                # go back 5 frames approximately
                if check_valid(stimes[0]): 
                    begin = stimes[0]
                else:
                    if check_valid(etimes[0]): 
                        begin = max(etimes[0] - 0.05,0) 
                        stimes[0] = begin
                    elif check_valid(stimes[1]):
                        begin = max(stimes[1] - 0.05,0)
                        stimes[0] = begin
                    else:
                        continue

                if check_valid(etimes[-1]): 
                    end = etimes[-1]
                else:
                    if check_valid(stimes[-1]): 
                        end = stimes[-1] + 0.05
                        etimes[-1] = end
                    elif check_valid(etimes[-2]):
                        end = etimes[-2] + 0.05
                        etimes[-1] = end
                    else:
                        continue
                
                s_ms = begin*1000 # in msec
                e_ms = end*1000

                s_frame = int(np.floor(s_ms / hop))
                e_frame = int(np.ceil(e_ms / hop))
                pf_frames = pitchs[s_frame:e_frame] 

                # final clean up
                stimes, etimes = clean_up(stimes, etimes)
                assert len(stimes) == len(etimes) == len(tokens)

                sframes = [int(np.floor(x*100)) for x in stimes]
                eframes = [int(np.ceil(x*100)) for x in etimes]
                word_lengths = [e-s for s,e in zip(sframes,eframes)]
                invalid = [x for x in word_lengths if x <=0]
                if len(invalid)>0: 
                    print begin, stimes[0], etimes[0] 
                    print 
                    continue
                #print wlens
                wlens += word_lengths

                # TESTS PASSED
                if find_bad_alignment(stimes): 
                    six = find_bad_alignment(stimes)
                    print [tokens[i] for i in six], stimes, etimes 
                if find_bad_alignment(etimes): 
                    eix = find_bad_alignment(etimes)
                    print [tokens[i] for i in eix], stimes, etimes

                seq_len = len(pf_frames)
                lengths.append(seq_len)

    #num5 = len([x for x in lengths if x<5])
    #num25 = len([x for x in lengths if x<25])
    #num100 = len([x for x in lengths if x<100])
    num2000 = len([x for x in lengths if x>=2000])
    num3000 = len([x for x in lengths if x>=3000])
    num3500 = len([x for x in lengths if x>=3500])
    
    lengths = np.array(lengths)
    #print split, np.mean(lengths), max(lengths), min(lengths)
    #print num5, num25, num100, len(lengths)
    print "Number of sentences with >= 2000/3000/3500 frames:"
    print num2000, num3000, num3500
    
    #num5 = len([x for x in wlens if x<5])
    #num25 = len([x for x in wlens if x<25])
    #num100 = len([x for x in wlens if x<100])
    #wlens = np.array(wlens)
    #print split, np.mean(wlens), max(wlens), min(wlens)
    #print num5, num25, num100, len(wlens)



# The following functions perform data processing from raw in the steps:
# 1. look up timing to get appropriate mfcc and pitch frames
# 2. convert sentences to token ids
# 3. convert parses to token ids
# 4. put everything in a dictionary
def split_frames(split, feat_types):
    data_file = os.path.join(time_dir, split + '.data.csv')
    df = pandas.read_csv(data_file, sep='\t')
    sw_files = set(df.file_id.values)
    for sw in sw_files:
        this_dict = defaultdict(dict) 
        for speaker in ['A', 'B']:
            mfcc_file = os.path.join(mfcc_dir, sw + '-' + speaker + '.pickle')
            pitch_file = os.path.join(pitch_dir, sw + '-' + speaker + '.pickle')
            pitch_pov_file = os.path.join(pitch_pov_dir,sw+'-'+speaker+'.pickle')
            fbank_file = os.path.join(fbank_dir, sw + '-' +speaker+'.pickle')

            if 'mfcc' in feat_types:
                try:
                    data_mfcc = pickle.load(open(mfcc_file))
                except: 
                    print("No mfcc file for ", sw, speaker)
                    continue
                mfccs = data_mfcc.values()[0]
            if 'pitch2' in feat_types:
                try:
                    data_pitch = pickle.load(open(pitch_file))
                except: 
                    print("No pitch file for ", sw, speaker)
                    continue
                pitchs = data_pitch.values()[0]
            if 'pitch3' in feat_types:
                try:
                    data_pitch_pov = pickle.load(open(pitch_pov_file))
                except: 
                    print("No pitch pov file for ", sw, speaker)
                pitch_povs = data_pitch_pov.values()[0]
            if 'fbank' in feat_types:
                try:
                    data_fbank = pickle.load(open(fbank_file))
                except: 
                    print("No fbank file for ", sw, speaker)
                    continue
                fbanks = data_fbank.values()[0]

            this_df = df[(df.file_id==sw)&(df.speaker==speaker)]
            for i, row in this_df.iterrows():
                tokens = row.sentence.strip().split()
                stimes = convert_to_array(row.start_times)
                etimes = convert_to_array(row.end_times)
                
                if len(stimes)==1: 
                    if (not check_valid(stimes[0])) or (not check_valid(etimes[0])):
                        continue
                 
                # go back 5 frames approximately
                if check_valid(stimes[0]): 
                    begin = stimes[0]
                else:
                    if check_valid(etimes[0]): 
                        begin = max(etimes[0] - 0.05,0) 
                        stimes[0] = begin
                    elif check_valid(stimes[1]):
                        begin = max(stimes[1] - 0.05,0)
                        stimes[0] = begin
                    else:
                        continue

                if check_valid(etimes[-1]): 
                    end = etimes[-1]
                else:
                    if check_valid(stimes[-1]): 
                        end = stimes[-1] + 0.05
                        etimes[-1] = end
                    elif check_valid(etimes[-2]):
                        end = etimes[-2] + 0.05
                        etimes[-1] = end
                    else:
                        continue
                
                #s_ms = begin*1000 # in msec
                #e_ms = end*1000
                #s_frame = int(np.floor(s_ms / hop))
                #e_frame = int(np.ceil(e_ms / hop))
                
                # final clean up
                stimes, etimes = clean_up(stimes, etimes)
                assert len(stimes) == len(etimes) == len(tokens)

                sframes = [int(np.floor(x*100)) for x in stimes]
                eframes = [int(np.ceil(x*100)) for x in etimes]
                s_frame = sframes[0]
                e_frame = eframes[-1]
                word_lengths = [e-s for s,e in zip(sframes,eframes)]
                invalid = [x for x in word_lengths if x <=0]
                if len(invalid)>0: 
                    print begin, stimes[0], etimes[0] 
                    print 
                    continue

                offset = s_frame
                word_bounds = [(x-offset,y-offset) for x,y in zip(sframes, eframes)]
                assert len(word_bounds) == len(tokens)
                globID = row.sent_id.replace('~','_'+speaker+'_')
                this_dict[globID]['sents'] = row.sentence
                this_dict[globID]['parse'] = row.parse
                this_dict[globID]['windices'] = word_bounds
                for feat in feat_types:
                    if feat=='mfcc':
                        mf_frames = mfccs[s_frame:e_frame]
                        this_dict[globID]['mfccs'] = mf_frames
                    if feat=='pitch2':
                        pf_frames = pitchs[s_frame:e_frame]
                        this_dict[globID]['pitch2'] = pf_frames
                    if feat=='pitch3':
                        pv_frames = pitch_povs[s_frame:e_frame]
                        this_dict[globID]['pitch3'] = pv_frames
                    if feat=='fbank':
                        fb_frames = fbanks[s_frame:e_frame]
                        this_dict[globID]['fbank'] = fb_frames

        dict_name = os.path.join(output_dir, split, sw + '_word_level.pickle')
        pickle.dump(this_dict, open(dict_name, 'w'))


def process_data_both(data_dir, split, sent_vocab, parse_vocab, acoustic):
    data_set = [[] for _ in _buckets]
    split_path = os.path.join(data_dir, split)
    split_files = glob.glob(split_path + "/*")
    for file_path in split_files:
        this_data = pickle.load(open(file_path))
        for k in this_data.keys():
            sentence = this_data[k]['sents']
            parse = this_data[k]['parse']
            windices = this_data[k]['windices']
            #mfccs = make_array(this_data[k]['mfccs'])
            #pitch2 = make_array(this_data[k]['pitch2'])
            pitch3 = make_array(this_data[k]['pitch3'])
            #fbank = make_array(this_data[k]['fbank'])
            sent_ids = sentence_to_token_ids(sentence, sent_vocab, True, True)
            parse_ids = sentence_to_token_ids(parse, parse_vocab, False, False)
            if split != 'extra':
                parse_ids.append(EOS_ID)
            maybe_buckets = [b for b in xrange(len(_buckets)) 
                if _buckets[b][0] >= len(sent_ids) and _buckets[b][1] >= len(parse_ids)]
            if not maybe_buckets: 
                #print(k, sentence, parse)
                continue
            bucket_id = min(maybe_buckets)
            if acoustic == 'all':
                data_set[bucket_id].append([sent_ids, parse_ids, windices, mfccs, pitch2, pitch3])
            elif acoustic == 'mfcc':
                data_set[bucket_id].append([sent_ids, parse_ids, windices, mfccs])
            elif acoustic == 'fbank':
                data_set[bucket_id].append([sent_ids, parse_ids, windices, fbank])
            elif acoustic == 'pitch2':
                data_set[bucket_id].append([sent_ids, parse_ids, windices, pitch2])
            else: #acoustic == 'pitch3'
                data_set[bucket_id].append([sent_ids, parse_ids, windices, pitch3])

    return data_set

def map_sentences(data_dir, split):
    mappings = [[] for _ in _buckets]
    split_path = os.path.join(data_dir, split)
    split_files = glob.glob(split_path + "/*")
    for file_path in split_files:
        this_data = pickle.load(open(file_path))
        for k in this_data.keys():
            sentence = this_data[k]['sents']
            parse = this_data[k]['parse']
            sent_ids = sentence.rstrip().split() 
            parse_ids = parse.rstrip().split()
            #include line below for swbd_new, but not for swbd_speech and swbd_tune
            if split != 'extra':
                parse_ids.append(EOS_ID)
            maybe_buckets = [b for b in xrange(len(_buckets)) 
                if _buckets[b][0] >= len(sent_ids) and _buckets[b][1] >= len(parse_ids)]
                #if _buckets[b][0] > len(sent_ids) and _buckets[b][1] > len(parse_ids)]
            # > for swbd_speech; >= for swbd_new
            if not maybe_buckets: 
                #print(k, sentence, parse)
                continue
            bucket_id = min(maybe_buckets)
            mappings[bucket_id].append(k)
    return mappings 


def main(_):
    print "Check dev2"
    get_stats('dev2')
    print "\nCheck dev"
    get_stats('dev')
    print "\nCheck test"
    get_stats('test')
    print "\nCheck train"
    get_stats('train')

'''  
    sent_vocabulary_path = os.path.join(data_dir, 'vocab.sents') 
    parse_vocabulary_path = os.path.join(data_dir, 'vocab.parse')
    parse_vocab, _ = initialize_vocabulary(parse_vocabulary_path)
    sent_vocab, _ = initialize_vocabulary(sent_vocabulary_path)
   
    split = 'train'
    acoustic = 'pitch3'
    split_frames(split, [acoustic])  # ==> dumps to output_dir
    this_set = process_data_both(output_dir, split, sent_vocab, parse_vocab, \
            acoustic)
    this_file = os.path.join(output_dir, split + '_' + acoustic + '.pickle')
    pickle.dump(this_set, open(this_file,'w'))
'''

if __name__ == "__main__":
    tf.app.run()


