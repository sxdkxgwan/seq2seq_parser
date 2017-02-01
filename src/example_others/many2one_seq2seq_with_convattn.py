# Copyright 2015 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

"""Library for creating sequence-to-sequence models in TensorFlow.

Sequence-to-sequence recurrent neural networks can learn complex functions
that map input sequences to output sequences. These models yield very good
results on a number of tasks, such as speech recognition, parsing, machine
translation, or even constructing automated replies to emails.

Before using this module, it is recommended to read the TensorFlow tutorial
on sequence-to-sequence models. It explains the basic concepts of this module
and shows an end-to-end example of how to build a translation model.
  https://www.tensorflow.org/versions/master/tutorials/seq2seq/index.html

Here is an overview of functions available in this module. They all use
a very similar interface, so after reading the above tutorial and using
one of them, others should be easy to substitute.

* Full sequence-to-sequence models.
  - basic_rnn_seq2seq: The most basic RNN-RNN model.
  - tied_rnn_seq2seq: The basic model with tied encoder and decoder weights.
  - embedding_rnn_seq2seq: The basic model with input embedding.
  - embedding_tied_rnn_seq2seq: The tied model with input embedding.
  - embedding_attention_seq2seq: Advanced model with input embedding and
      the neural attention mechanism; recommended for complex tasks.

* Multi-task sequence-to-sequence models.
  - one2many_rnn_seq2seq: The embedding model with multiple decoders.

* Decoders (when you write your own encoder, you can use these to decode;
    e.g., if you want to write a model that generates captions for images).
  - rnn_decoder: The basic decoder based on a pure RNN.
  - attention_decoder: A decoder that uses the attention mechanism.

* Losses.
  - sequence_loss: Loss for a sequence model returning average log-perplexity.
  - sequence_loss_by_example: As above, but not averaging over all examples.

* model_with_buckets: A convenience function to create models with bucketing
    (see the tutorial above for an explanation of why and how to use it).
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

# We disable pylint because we need python3 compatibility.
from six.moves import xrange  # pylint: disable=redefined-builtin
from six.moves import zip     # pylint: disable=redefined-builtin

from tensorflow.python.framework import dtypes
from tensorflow.python.framework import ops
from tensorflow.python.ops import array_ops
from tensorflow.python.ops import control_flow_ops
from tensorflow.python.ops import embedding_ops
from tensorflow.python.ops import math_ops
from tensorflow.python.ops import nn_ops
from tensorflow.python.ops import rnn
from tensorflow.python.ops import rnn_cell
from tensorflow.python.ops import variable_scope

# TODO(ebrevdo): Remove once _linear is fully deprecated.
linear = rnn_cell._linear  # pylint: disable=protected-access


def _extract_argmax_and_embed(embedding, output_projection=None,
                              update_embedding=True):
  """Get a loop_function that extracts the previous symbol and embeds it.

  Args:
    embedding: embedding tensor for symbols.
    output_projection: None or a pair (W, B). If provided, each fed previous
      output will first be multiplied by W and added B.
    update_embedding: Boolean; if False, the gradients will not propagate
      through the embeddings.

  Returns:
    A loop function.
  """
  def loop_function(prev, _):
    if output_projection is not None:
      prev = nn_ops.xw_plus_b(
          prev, output_projection[0], output_projection[1])
    prev_symbol = math_ops.argmax(prev, 1)
    # Note that gradients will not propagate through the second parameter of
    # embedding_lookup.
    emb_prev = embedding_ops.embedding_lookup(embedding, prev_symbol)
    if not update_embedding:
      emb_prev = array_ops.stop_gradient(emb_prev)
    return emb_prev
  return loop_function


def attention_decoder(decoder_inputs, initial_state, attention_states, cell,
                    use_conv=False, conv_filter_width=10, conv_num_channels=10,
                    attn_vec_size=64, output_size=None, num_heads=1, 
                    loop_function=None, dtype=dtypes.float32, scope=None,
                    initial_state_attention=False, attention_vec_size=None):
  if not decoder_inputs:
    raise ValueError("Must provide at least 1 input to attention decoder.")
  if num_heads < 1:
    raise ValueError("With less than 1 heads, use a non-attention decoder.")
  if not attention_states.get_shape()[1:2].is_fully_defined():
    raise ValueError("Shape[1] and [2] of attention_states must be known: %s"
                     % attention_states.get_shape())
  if output_size is None:
    output_size = cell.output_size

  with variable_scope.variable_scope(scope or "attention_decoder"):
    batch_size = array_ops.shape(decoder_inputs[0])[0]  # Needed for reshaping.
    attn_length = attention_states.get_shape()[1].value
    attn_size = attention_states.get_shape()[2].value

    # To calculate W1 * h_t we use a 1-by-1 convolution, need to reshape before.
    hidden = array_ops.reshape(
        attention_states, [-1, attn_length, 1, attn_size])
    hidden_features = []
    v = []
    if use_conv:
        F = []
        U = []

    if not attn_vec_size: 
        attention_vec_size = attn_size
    else:
        attention_vec_size = attn_vec_size

    print ("Attention vector size: %d" %attention_vec_size)
    print ("Conv filter width: %d" %conv_filter_width)
    print ("Conv num channels: %d" %conv_num_channels)


    for a in xrange(num_heads):
      k = variable_scope.get_variable("AttnW_%d" % a,
                                      [1, 1, attn_size, attention_vec_size])
      hidden_features.append(nn_ops.conv2d(hidden, k, [1, 1, 1, 1], "SAME"))
      v.append(variable_scope.get_variable("AttnV_%d" % a, [attention_vec_size]))
      if use_conv:
        F.append(variable_scope.get_variable("AttnF_%d" % a, 
                                    [conv_filter_width, 1, 1, conv_num_channels]))   
        U.append(variable_scope.get_variable("AttnU_%d" % a, 
                                    [1, 1, conv_num_channels, attention_vec_size]))
    
        
    state = initial_state

    def attention(query, prev_alpha):
      """Put attention masks on hidden using hidden_features and query."""
      ds = []  # Results of attention reads will be stored here.
      alphas = []
      for a in xrange(num_heads):
        with variable_scope.variable_scope("Attention_%d" % a):
          y = linear(query, attention_vec_size, True)
          y = array_ops.reshape(y, [-1, 1, 1, attention_vec_size])
          if use_conv:
            conv_features = nn_ops.conv2d(prev_alpha[a], F[a], [1, 1, 1, 1], "SAME")
            feat_reshape = nn_ops.conv2d(conv_features, U[a], [1, 1, 1, 1], "SAME")

            # Attention mask is a softmax of v^T * tanh(...).
            s = math_ops.reduce_sum(
                v[a] * math_ops.tanh(hidden_features[a] + y + feat_reshape), [2, 3])
          else:
            s = math_ops.reduce_sum(
                v[a] * math_ops.tanh(hidden_features[a] + y), [2, 3])

          alpha = nn_ops.softmax(s)
          alpha = array_ops.reshape(alpha, [-1, attn_length, 1, 1])
          alphas.append(alpha)
          # Now calculate the attention-weighted vector d.
          d = math_ops.reduce_sum(alpha * hidden, [1, 2])
          ds.append(array_ops.reshape(d, [-1, attn_size]))
      return ds, alphas

    outputs = []
    prev = None
    batch_attn_size = array_ops.pack([batch_size, attn_size])
    attns = [array_ops.zeros(batch_attn_size, dtype=dtype)
             for _ in xrange(num_heads)]
    for a in attns:  # Ensure the second shape of attention vectors is set.
      a.set_shape([None, attn_size])

    batch_alpha_size = array_ops.pack([batch_size, attn_length, 1, 1])
    alphas = [array_ops.zeros(batch_alpha_size, dtype=dtype)
             for _ in xrange(num_heads)]
    for a in alphas:  
      a.set_shape([None, attn_length, 1, 1])

    for i, inp in enumerate(decoder_inputs):
      if i > 0:
        variable_scope.get_variable_scope().reuse_variables()
      # If loop_function is set, we use it instead of decoder_inputs.
      if loop_function is not None and prev is not None:
        with variable_scope.variable_scope("loop_function", reuse=True):
          inp = loop_function(prev, i)
      # Merge input and previous attentions into one vector of the right size.
      input_size = inp.get_shape().with_rank(2)[1]
      if input_size.value is None:
        raise ValueError("Could not infer input size from input: %s" % inp.name)
      x = linear([inp] + attns, input_size, True)
      # Run the RNN.
      cell_output, state = cell(x, state)
      # Run the attention mechanism.
      if i == 0 and initial_state_attention:
        with variable_scope.variable_scope(variable_scope.get_variable_scope(),
                                           reuse=True):
          attns, alphas = attention(cell_output, alphas)
      else:
        attns, alphas = attention(cell_output, alphas)

      with variable_scope.variable_scope("AttnOutputProjection"):
        output = linear([cell_output] + attns, output_size, True)
      if loop_function is not None:
        prev = output
      outputs.append(output)

  return outputs, state


def embedding_attention_decoder(decoder_inputs, initial_state, attention_states,
                                cell, num_symbols, embedding_size, 
                                use_conv=False, conv_filter_width=5, 
                                conv_num_channels=20, attn_vec_size=64, 
                                num_heads=1,
                                output_size=None, output_projection=None,
                                feed_previous=False,
                                update_embedding_for_previous=True,
                                dtype=dtypes.float32, scope=None,
                                initial_state_attention=False):
  if output_size is None:
    output_size = cell.output_size
  if output_projection is not None:
    proj_biases = ops.convert_to_tensor(output_projection[1], dtype=dtype)
    proj_biases.get_shape().assert_is_compatible_with([num_symbols])

  with variable_scope.variable_scope(scope or "embedding_attention_decoder"):
    embedding = variable_scope.get_variable("embedding",
            [num_symbols, embedding_size])
    loop_function = _extract_argmax_and_embed(
        embedding, output_projection,
        update_embedding_for_previous) if feed_previous else None
    emb_inp = [
        embedding_ops.embedding_lookup(embedding, i) for i in decoder_inputs]
    return attention_decoder(
        emb_inp, initial_state, attention_states, cell, 
        use_conv=use_conv, conv_filter_width=conv_filter_width,  ## Convolution feature param
        conv_num_channels=conv_num_channels, 
        attn_vec_size=attn_vec_size,                    
        output_size=output_size,
        num_heads=num_heads, loop_function=loop_function,
        initial_state_attention=initial_state_attention)


def embedding_attention_seq2seq(encoder_inputs, decoder_inputs, seq_len, cell,
                                num_encoder_symbols, num_decoder_symbols,
                                embedding_size,
                                use_conv=False, conv_filter_width=5, 
                                conv_num_channels=20, attn_vec_size=64, 
                                num_heads=1, output_projection=None,
                                feed_previous=False, dtype=dtypes.float32,
                                scope=None, initial_state_attention=False):
  with variable_scope.variable_scope(scope or "embedding_attention_seq2seq"):
    embedding_words = variable_scope.get_variable("embedding_words",
            [num_encoder_symbols, embedding_size])
    
    ## We need to do the embedding beforehand so that the rnn infers the input type 
    ## to be float and doesn't cause trouble in copying state after sequence length 
    ## This issue has been fixed in 0.10 version
    ## The issue is referred here - https://github.com/tensorflow/tensorflow/issues/3322
    encoder_inputs = [
        embedding_ops.embedding_lookup(embedding_words, i) for i in encoder_inputs]
    encoder_outputs, encoder_state = rnn.rnn(
            cell, encoder_inputs, sequence_length=seq_len, dtype=dtype)

    
    # First calculate a concatenation of encoder outputs to put attention on.
    top_states = [array_ops.reshape(e, [-1, 1, cell.output_size])
                  for e in encoder_outputs]
    attention_states = array_ops.concat(1, top_states)

    # Decoder.
    output_size = None
    if output_projection is None:
      cell = rnn_cell.OutputProjectionWrapper(cell, num_decoder_symbols)
      output_size = num_decoder_symbols

    if isinstance(feed_previous, bool):
      return embedding_attention_decoder(
          decoder_inputs, encoder_state, attention_states, cell,
          num_decoder_symbols, embedding_size, 
          use_conv=use_conv, conv_filter_width=conv_filter_width,
          conv_num_channels=conv_num_channels,
          attn_vec_size=attn_vec_size,
          num_heads=num_heads,
          output_size=output_size, output_projection=output_projection,
          feed_previous=feed_previous,
          initial_state_attention=initial_state_attention)


def one2many_rnn_seq2seq(encoder_inputs, decoder_inputs_dict, cell,
                         num_encoder_symbols, num_decoder_symbols_dict,
                         embedding_size, feed_previous=False,
                         dtype=dtypes.float32, scope=None):
  """One-to-many RNN sequence-to-sequence model (multi-task).

  This is a multi-task sequence-to-sequence model with one encoder and multiple
  decoders. Reference to multi-task sequence-to-sequence learning can be found
  here: http://arxiv.org/abs/1511.06114

  Args:
    encoder_inputs: A list of 1D int32 Tensors of shape [batch_size].
    decoder_inputs_dict: A dictionany mapping decoder name (string) to
      the corresponding decoder_inputs; each decoder_inputs is a list of 1D
      Tensors of shape [batch_size]; num_decoders is defined as
      len(decoder_inputs_dict).
    cell: rnn_cell.RNNCell defining the cell function and size.
    num_encoder_symbols: Integer; number of symbols on the encoder side.
    num_decoder_symbols_dict: A dictionary mapping decoder name (string) to an
      integer specifying number of symbols for the corresponding decoder;
      len(num_decoder_symbols_dict) must be equal to num_decoders.
    embedding_size: Integer, the length of the embedding vector for each symbol.
    feed_previous: Boolean or scalar Boolean Tensor; if True, only the first of
      decoder_inputs will be used (the "GO" symbol), and all other decoder
      inputs will be taken from previous outputs (as in embedding_rnn_decoder).
      If False, decoder_inputs are used as given (the standard decoder case).
    dtype: The dtype of the initial state for both the encoder and encoder
      rnn cells (default: tf.float32).
    scope: VariableScope for the created subgraph; defaults to
      "one2many_rnn_seq2seq"

  Returns:
    A tuple of the form (outputs_dict, state_dict), where:
      outputs_dict: A mapping from decoder name (string) to a list of the same
        length as decoder_inputs_dict[name]; each element in the list is a 2D
        Tensors with shape [batch_size x num_decoder_symbol_list[name]]
        containing the generated outputs.
      state_dict: A mapping from decoder name (string) to the final state of the
        corresponding decoder RNN; it is a 2D Tensor of shape
        [batch_size x cell.state_size].
  """
  outputs_dict = {}
  state_dict = {}

  with variable_scope.variable_scope(scope or "one2many_rnn_seq2seq"):
    # Encoder.
    encoder_cell = rnn_cell.EmbeddingWrapper(
        cell, embedding_classes=num_encoder_symbols,
        embedding_size=embedding_size)
    _, encoder_state = rnn.rnn(encoder_cell, encoder_inputs, dtype=dtype)

    # Decoder.
    for name, decoder_inputs in decoder_inputs_dict.items():
      num_decoder_symbols = num_decoder_symbols_dict[name]

      with variable_scope.variable_scope("one2many_decoder_" + str(name)):
        decoder_cell = rnn_cell.OutputProjectionWrapper(cell,
                                                        num_decoder_symbols)
        if isinstance(feed_previous, bool):
          outputs, state = embedding_rnn_decoder(
              decoder_inputs, encoder_state, decoder_cell, num_decoder_symbols,
              embedding_size, feed_previous=feed_previous)
        else:
          # If feed_previous is a Tensor, we construct 2 graphs and use cond.
          def filled_embedding_rnn_decoder(feed_previous):
            # pylint: disable=cell-var-from-loop
            reuse = None if feed_previous else True
            vs = variable_scope.get_variable_scope()
            with variable_scope.variable_scope(vs, reuse=reuse):
              outputs, state = embedding_rnn_decoder(
                  decoder_inputs, encoder_state, decoder_cell,
                  num_decoder_symbols, embedding_size,
                  feed_previous=feed_previous)
            # pylint: enable=cell-var-from-loop
            return outputs + [state]
          outputs_and_state = control_flow_ops.cond(
              feed_previous,
              lambda: filled_embedding_rnn_decoder(True),
              lambda: filled_embedding_rnn_decoder(False))
          outputs = outputs_and_state[:-1]
          state = outputs_and_state[-1]

      outputs_dict[name] = outputs
      state_dict[name] = state

  return outputs_dict, state_dict


def sequence_loss_by_example(logits, targets, weights,
                             average_across_timesteps=True,
                             softmax_loss_function=None, name=None):
  """Weighted cross-entropy loss for a sequence of logits (per example).

  Args:
    logits: List of 2D Tensors of shape [batch_size x num_decoder_symbols].
    targets: List of 1D batch-sized int32 Tensors of the same length as logits.
    weights: List of 1D batch-sized float-Tensors of the same length as logits.
    average_across_timesteps: If set, divide the returned cost by the total
      label weight.
    softmax_loss_function: Function (inputs-batch, labels-batch) -> loss-batch
      to be used instead of the standard softmax (the default if this is None).
    name: Optional name for this operation, default: "sequence_loss_by_example".

  Returns:
    1D batch-sized float Tensor: The log-perplexity for each sequence.

  Raises:
    ValueError: If len(logits) is different from len(targets) or len(weights).
  """
  if len(targets) != len(logits) or len(weights) != len(logits):
    raise ValueError("Lengths of logits, weights, and targets must be the same "
                     "%d, %d, %d." % (len(logits), len(weights), len(targets)))
  with ops.name_scope(name, "sequence_loss_by_example", logits + targets + weights):
    log_perp_list = []
    for logit, target, weight in zip(logits, targets, weights):
      if softmax_loss_function is None:
        # TODO(irving,ebrevdo): This reshape is needed because
        # sequence_loss_by_example is called with scalars sometimes, which
        # violates our general scalar strictness policy.
        target = array_ops.reshape(target, [-1])
        crossent = nn_ops.sparse_softmax_cross_entropy_with_logits(
            logit, target)
      else:
        crossent = softmax_loss_function(logit, target)
      log_perp_list.append(crossent * weight)
    log_perps = math_ops.add_n(log_perp_list)
    if average_across_timesteps:
      total_size = math_ops.add_n(weights)
      total_size += 1e-12  # Just to avoid division by 0 for all-0 weights.
      log_perps /= total_size
  return log_perps


def sequence_loss(logits, targets, weights,
                  average_across_timesteps=True, average_across_batch=True,
                  softmax_loss_function=None, name=None):
  """Weighted cross-entropy loss for a sequence of logits, batch-collapsed.

  Args:
    logits: List of 2D Tensors of shape [batch_size x num_decoder_symbols].
    targets: List of 1D batch-sized int32 Tensors of the same length as logits.
    weights: List of 1D batch-sized float-Tensors of the same length as logits.
    average_across_timesteps: If set, divide the returned cost by the total
      label weight.
    average_across_batch: If set, divide the returned cost by the batch size.
    softmax_loss_function: Function (inputs-batch, labels-batch) -> loss-batch
      to be used instead of the standard softmax (the default if this is None).
    name: Optional name for this operation, defaults to "sequence_loss".

  Returns:
    A scalar float Tensor: The average log-perplexity per symbol (weighted).

  Raises:
    ValueError: If len(logits) is different from len(targets) or len(weights).
  """
  with ops.name_scope(name, "sequence_loss", logits + targets + weights):
    cost = math_ops.reduce_sum(sequence_loss_by_example(
        logits, targets, weights,
        average_across_timesteps=average_across_timesteps,
        softmax_loss_function=softmax_loss_function))
    if average_across_batch:
      batch_size = array_ops.shape(targets[0])[0]
      return cost / math_ops.cast(batch_size, dtypes.float32)
    else:
      return cost

def many2one_model_with_buckets(encoder_inputs_list, decoder_inputs, targets, weights,
                       buckets, seq2seq, softmax_loss_function=None,
                       per_example_loss=False, name=None, spscale=20):
  
  # Modified model with buckets to accept 2 encoders

  if len(encoder_inputs_list[0]) < buckets[-1][0]:
    raise ValueError("Length of encoder_inputs (%d) must be at least that of la"
                     "st bucket (%d)." % (len(encoder_inputs), buckets[-1][0]))
  if len(targets) < buckets[-1][1]:
    raise ValueError("Length of targets (%d) must be at least that of last"
                     "bucket (%d)." % (len(targets), buckets[-1][1]))
  if len(weights) < buckets[-1][1]:
    raise ValueError("Length of weights (%d) must be at least that of last"
                     "bucket (%d)." % (len(weights), buckets[-1][1]))

  all_inputs = encoder_inputs_list + decoder_inputs + targets + weights
  losses = []
  outputs = []
  speech_buckets = [(x*spscale, y) for (x,y) in buckets]
  with ops.name_scope(name, "many2one_model_with_buckets", all_inputs):
    for j, bucket in enumerate(buckets):
      with variable_scope.variable_scope(variable_scope.get_variable_scope(),
                                         reuse=True if j > 0 else None):
        #bucket_outputs, _ = seq2seq(encoder_inputs[:bucket[0]], decoder_inputs[:bucket[1]])
        x = encoder_inputs_list[0][:bucket[0]]
        #print( x )
        y = encoder_inputs_list[1][:speech_buckets[j][0]]
        bucket_outputs, _ = seq2seq([x, y], decoder_inputs[:bucket[1]])
        outputs.append(bucket_outputs)
        if per_example_loss:
          losses.append(sequence_loss_by_example(
              outputs[-1], targets[:bucket[1]], weights[:bucket[1]],
              softmax_loss_function=softmax_loss_function))
        else:
          losses.append(sequence_loss(
              outputs[-1], targets[:bucket[1]], weights[:bucket[1]],
              softmax_loss_function=softmax_loss_function))

  return outputs, losses



def model_with_buckets(encoder_inputs, decoder_inputs, targets, weights, seq_len, 
                       buckets, seq2seq, softmax_loss_function=None,
                       per_example_loss=False, name=None):
  """Create a sequence-to-sequence model with support for bucketing.

  The seq2seq argument is a function that defines a sequence-to-sequence model,
  e.g., seq2seq = lambda x, y: basic_rnn_seq2seq(x, y, rnn_cell.GRUCell(24))

  Args:
    encoder_inputs: A list of Tensors to feed the encoder; first seq2seq input.
    decoder_inputs: A list of Tensors to feed the decoder; second seq2seq input.
    targets: A list of 1D batch-sized int32 Tensors (desired output sequence).
    weights: List of 1D batch-sized float-Tensors to weight the targets.
    buckets: A list of pairs of (input size, output size) for each bucket.
    seq2seq: A sequence-to-sequence model function; it takes 2 input that
      agree with encoder_inputs and decoder_inputs, and returns a pair
      consisting of outputs and states (as, e.g., basic_rnn_seq2seq).
    softmax_loss_function: Function (inputs-batch, labels-batch) -> loss-batch
      to be used instead of the standard softmax (the default if this is None).
    per_example_loss: Boolean. If set, the returned loss will be a batch-sized
      tensor of losses for each sequence in the batch. If unset, it will be
      a scalar with the averaged loss from all examples.
    name: Optional name for this operation, defaults to "model_with_buckets".

  Returns:
    A tuple of the form (outputs, losses), where:
      outputs: The outputs for each bucket. Its j'th element consists of a list
        of 2D Tensors of shape [batch_size x num_decoder_symbols] (jth outputs).
      losses: List of scalar Tensors, representing losses for each bucket, or,
        if per_example_loss is set, a list of 1D batch-sized float Tensors.

  Raises:
    ValueError: If length of encoder_inputsut, targets, or weights is smaller
      than the largest (last) bucket.
  """
  if len(encoder_inputs) < buckets[-1][0]:
    raise ValueError("Length of encoder_inputs (%d) must be at least that of la"
                     "st bucket (%d)." % (len(encoder_inputs), buckets[-1][0]))
  if len(targets) < buckets[-1][1]:
    raise ValueError("Length of targets (%d) must be at least that of last"
                     "bucket (%d)." % (len(targets), buckets[-1][1]))
  if len(weights) < buckets[-1][1]:
    raise ValueError("Length of weights (%d) must be at least that of last"
                     "bucket (%d)." % (len(weights), buckets[-1][1]))

  all_inputs = encoder_inputs + decoder_inputs + targets + weights
  losses = []
  outputs = []
  with ops.name_scope(name, "model_with_buckets", all_inputs):
    for j, bucket in enumerate(buckets):
      with variable_scope.variable_scope(variable_scope.get_variable_scope(),
                                         reuse=True if j > 0 else None):
        bucket_outputs, _ = seq2seq(encoder_inputs[:bucket[0]],
                                    decoder_inputs[:bucket[1]],
                                    seq_len)

        outputs.append(bucket_outputs)
        if per_example_loss:
          losses.append(sequence_loss_by_example(
              outputs[-1], targets[:bucket[1]], weights[:bucket[1]],
              softmax_loss_function=softmax_loss_function))
        else:
          losses.append(sequence_loss(
              outputs[-1], targets[:bucket[1]], weights[:bucket[1]],
              softmax_loss_function=softmax_loss_function))

  return outputs, losses
