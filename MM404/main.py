#!/usr/bin/env python
# -*- coding: utf-8 -*-

# MM404 - main.py
# Minutes Made, Copyright 2019
# Maintainer: Eric Mikulin

import os
import uuid
import wave
import math
import audioop
import redis
import json
import numpy as np
from collections import deque
from flask import Flask, render_template, current_app, session, url_for, render_template
from flask_socketio import SocketIO, emit
from ltsd import LTSD_Detector

app = Flask(__name__)
app.config['FILEDIR'] = 'static/_files/'
socketio = SocketIO(app)

REDIS_Q_KEY = 'voice-chunk-queue'

SILENCE_LIMIT = 1.5  # Number of seconds of silence before audio phrase has ended
PREV_AUDIO = 1  # Amount of previous audio (in seconds) to prepend to the audio phrase
CHUNK = 2048
LTSD_CHUNK = int( CHUNK/2 )
LTSD_ORDER = 5

@app.route('/')
def index():
    """Return the client application. This frontend is used purely for testing purposes."""
    return render_template('index.html')

@socketio.on('start-recording')
def start_recording(options):
    """Start recording audio from the client."""
    print("Recording starts!")
    id = uuid.uuid4().hex  # server-side filename
    session['base_wavename'] = id

    # Set up the session variables for the audio detection
    session['options'] = options
    session['delta'] = options.get('fps', 44100) / CHUNK
    session['prev_audio'] = deque(maxlen=int(PREV_AUDIO * session['delta']))
    session['detect_win'] = deque(maxlen=int(SILENCE_LIMIT * session['delta']))
    session['captured_audio'] = []
    session['started'] = False

    session['ltsd'] = LTSD_Detector(LTSD_CHUNK, LTSD_ORDER)

    session['redisdb'] = redis.StrictRedis(host='redis', port=6379, db=0)

def chunks(l, n):
    """(Generator) Yield successive n-sized chunks from l."""
    for i in range(0, len(l), n):
        yield l[i:i + n]

@socketio.on('write-audio')
def detect_audio(data):
    """Voice activity detection of audio chunks from the client."""
    for bchunk in chunks(data, CHUNK):
        session['detect_win'].append(np.float32(np.fromstring(bchunk, np.int16)))

        # Buffer is not long enough yet
        if len(session['detect_win']) < session['detect_win'].maxlen:
            session['ltsd'].compute_noise_spectrum(list(session['detect_win']))  # Assume that the start of the recording is the noise
            print("Filling buffer")

        # Otherwise if speech is detected
        elif session['ltsd'].compute_window(list(session['detect_win'])):
            if not session['started']:
                session['started'] = True
                print("Speech starts!")
            session['captured_audio'].append(bchunk)

        # Otherwise no speech, but previsouly was detecting speech
        elif session['started'] is True:
            print("Speech ends!")
            # The limit was reached, finish capture and deliver.
            filename = write_speech(list(session['prev_audio']) + session['captured_audio'])

            # Reset audio detection session variables
            session['started'] = False
            session['prev_audio'].clear()
            session['captured_audio'] = []

        # No speech detected and no previous speech detected
        else:
            session['ltsd'].update_noise_spectrum(list(session['detect_win']))  # Update the noise spectrum
            session['prev_audio'].append(bchunk)

def write_speech(audio):
    """Writes audio data to a wavefile, returns the filename."""
    filename = session['base_wavename'] + "-" + uuid.uuid4().hex + ".wav"

    wf = wave.open(current_app.config['FILEDIR'] + filename, 'wb')
    wf.setnchannels(session['options'].get('numChannels', 1))
    wf.setsampwidth(session['options'].get('bps', 16) // 8)
    wf.setframerate(session['options'].get('fps', 44100))

    byte_stream_audio = b''.join(audio)
    wf.writeframes(byte_stream_audio)
    wf.close()

    # Emit link to wavfile via websockets
    audio_chunk_url = url_for('static', filename='_files/' + filename)
    emit('add-wavefile', audio_chunk_url)

    redis_payload = {'auth': session['base_wavename'], 'uri': audio_chunk_url, 'filename': filename}

    # Queue the audio chunk to be processed by REDIS
    session['redisdb'].lpush(REDIS_Q_KEY, json.dumps(redis_payload))

    return filename

@socketio.on('end-recording')
def end_recording():
    """Stop recording audio from the client."""
    print("Recording ends!")

    # Tear down the session variables
    del session['options']
    del session['delta']
    del session['base_wavename']
    del session['prev_audio']
    del session['detect_win']
    del session['captured_audio']
    del session['started']
    del session['ltsd']
    del session['redisdb']

@socketio.on('get-transcript')
def get_transcript():
    # Get the transcript from redis
    transcript_array_buffer = session['redisdb'].lrange(session['base_wavename'], 0, -1)
    transcript_array = [x.decode("utf-8") for x in transcript_array_buffer]
    emit('new-transcript', "\n".join(transcript_array))

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0')
