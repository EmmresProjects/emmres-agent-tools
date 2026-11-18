"""Dependency-free PCM WAV reader, analyzer, and conservative processor."""
import math
import os
import struct
import wave

MAX_FILE_BYTES = 128_000_000
MAX_FRAMES = 10_000_000
MAX_SAMPLE_VALUES = 8_000_000

class AudioError(ValueError):
    pass

def read_wav(path):
    path = os.fspath(path)
    size = os.path.getsize(path)
    if size > MAX_FILE_BYTES:
        raise AudioError(f"Input exceeds {MAX_FILE_BYTES} byte safety limit")
    with open(path, 'rb') as f:
        head = f.read(12)
        if len(head) != 12 or head[:4] != b'RIFF' or head[8:] != b'WAVE':
            raise AudioError('Only little-endian RIFF/WAVE files are supported')
        fmt = None; payload = None; remaining = max(0, size - 12)
        while remaining >= 8:
            chunk = f.read(8)
            if len(chunk) != 8: break
            cid, length = chunk[:4], struct.unpack('<I', chunk[4:])[0]
            remaining -= 8
            if length > remaining or length > MAX_FILE_BYTES:
                raise AudioError('Malformed WAV chunk length')
            data = f.read(length)
            remaining -= length
            if length & 1:
                f.read(1); remaining -= 1
            if cid == b'fmt ':
                if len(data) < 16: raise AudioError('Malformed WAV format chunk')
                fmt = struct.unpack('<HHIIHH', data[:16])
                if fmt[0] == 0xFFFE and len(data) >= 40:
                    # WAVE_FORMAT_EXTENSIBLE subtype GUID starts with the PCM/float code.
                    fmt = (struct.unpack('<H', data[24:26])[0],) + fmt[1:]
            elif cid == b'data':
                payload = data
        if not fmt or payload is None: raise AudioError('WAV is missing fmt or data chunk')
    code, channels, rate, byte_rate, align, bits = fmt
    if channels < 1 or channels > 32 or rate < 1 or rate > 768000:
        raise AudioError('Unsupported channel count or sample rate')
    if code not in (1, 3): raise AudioError(f'Unsupported WAV encoding code {code}; PCM and IEEE float only')
    if (code == 1 and bits not in (8, 16, 24, 32)) or (code == 3 and bits not in (32, 64)):
        raise AudioError(f'Unsupported sample width: {bits} bits')
    width = bits // 8
    if align != channels * width or len(payload) % align:
        raise AudioError('Invalid WAV block alignment or incomplete frame')
    frames = len(payload) // align
    if frames > MAX_FRAMES: raise AudioError(f'Audio exceeds {MAX_FRAMES} frame safety limit')
    if frames * channels > MAX_SAMPLE_VALUES: raise AudioError(f'Audio exceeds {MAX_SAMPLE_VALUES} decoded sample-value safety limit')
    values = []
    if code == 3:
        codefmt = '<f' if bits == 32 else '<d'
        values = [struct.unpack_from(codefmt, payload, i)[0] for i in range(0, len(payload), width)]
        if any(not math.isfinite(x) for x in values): raise AudioError('Audio contains non-finite float samples')
    elif bits == 8:
        values = [(x - 128) / 128.0 for x in payload]
    elif bits == 16:
        values = [x / 32768.0 for x in struct.unpack('<' + 'h' * (len(payload)//2), payload)]
    elif bits == 24:
        for i in range(0, len(payload), 3):
            v = payload[i] | payload[i+1] << 8 | payload[i+2] << 16
            if v & 0x800000: v -= 1 << 24
            values.append(v / 8388608.0)
    else:
        values = [x / 2147483648.0 for x in struct.unpack('<' + 'i' * (len(payload)//4), payload)]
    return {'samples': values, 'channels': channels, 'rate': rate, 'bits': bits, 'encoding': 'float' if code == 3 else 'pcm', 'frames': frames}

def write_wav(path, samples, channels, rate, bits=24):
    path = os.fspath(path)
    if bits not in (16, 24, 32): raise AudioError('Output PCM bit depth must be 16, 24, or 32')
    if channels < 1 or len(samples) % channels: raise AudioError('Sample count is not a whole number of frames')
    width = bits // 8; block = channels * width; data_size = len(samples) * width
    if data_size + 36 > 0xFFFFFFFF: raise AudioError('Output exceeds classic RIFF 4 GiB limit')
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + '.tmp'
    try:
        with open(tmp, 'wb') as f:
            f.write(b'RIFF' + struct.pack('<I', 36 + data_size) + b'WAVE')
            f.write(b'fmt ' + struct.pack('<IHHIIHH', 16, 1, channels, rate, rate*block, block, bits))
            f.write(b'data' + struct.pack('<I', data_size))
            if bits == 16:
                f.write(struct.pack('<' + 'h'*len(samples), *(max(-32768, min(32767, round(max(-1.0, min(1.0, x))*32768))) for x in samples)))
            elif bits == 24:
                buf = bytearray()
                for x in samples:
                    v = max(-8388608, min(8388607, round(max(-1.0, min(1.0, x))*8388608)))
                    if v < 0: v += 1 << 24
                    buf.extend((v & 255, (v >> 8) & 255, (v >> 16) & 255))
                f.write(buf)
            else:
                f.write(struct.pack('<' + 'i'*len(samples), *(max(-2147483648, min(2147483647, round(max(-1.0, min(1.0, x))*2147483648))) for x in samples)))
        os.replace(tmp, path)
    except Exception:
        try: os.remove(tmp)
        except OSError: pass
        raise

def analyze(data):
    x = data['samples']; n = len(x); ch = data['channels']; frames = data['frames']
    peak = max((abs(v) for v in x), default=0.0)
    rms = math.sqrt(sum(v*v for v in x) / n) if n else 0.0
    channel_stats = []
    for c in range(ch):
        a = x[c::ch]; p = max((abs(v) for v in a), default=0.0)
        r = math.sqrt(sum(v*v for v in a)/len(a)) if a else 0.0
        channel_stats.append({'channel': c+1, 'peak': p, 'peak_dbfs': db(p), 'rms': r, 'rms_dbfs': db(r), 'dc_offset': sum(a)/len(a) if a else 0.0, 'clipped_samples': sum(abs(v) >= 0.999 for v in a)})
    return {'format':'WAV', 'encoding':data['encoding'], 'sample_rate_hz':data['rate'], 'channels':ch, 'frames':frames, 'duration_seconds':frames/data['rate'], 'container_bit_depth':data['bits'], 'sample_peak':peak, 'sample_peak_dbfs':db(peak), 'rms':rms, 'rms_dbfs':db(rms), 'clipped_samples':sum(abs(v)>=0.999 for v in x), 'silence_samples':sum(abs(v)<1e-5 for v in x), 'silence_fraction':sum(abs(v)<1e-5 for v in x)/n if n else 0.0, 'channel_stats':channel_stats, 'measurement_note':'Sample-peak and sample-RMS only; not BS.1770 integrated loudness or true-peak metering.'}

def db(x): return 20*math.log10(x) if x > 0 else None

def process(data, operation, params):
    x = list(data['samples']); ch = data['channels']; rate=data['rate']; bits=int(params.get('bit_depth',24))
    if operation == 'normalize':
        target=float(params.get('target_dbfs', -1.0))
        if not math.isfinite(target) or not -24 <= target <= 0: raise AudioError('target_dbfs must be between -24 and 0 dBFS')
        peak=max((abs(v) for v in x),default=0)
        if peak == 0: raise AudioError('Cannot peak-normalize a silent file')
        factor=10**(target/20)/peak
        y=[v*factor for v in x]
        detail={'gain_db':20*math.log10(factor),'target_sample_peak_dbfs':target}
    elif operation == 'gain':
        gain=float(params.get('gain_db',0))
        if not math.isfinite(gain) or not -60 <= gain <= 24: raise AudioError('gain_db must be between -60 and +24')
        factor=10**(gain/20); y=[v*factor for v in x]; detail={'gain_db':gain}
    elif operation == 'trim':
        start=float(params.get('start_seconds',0)); end=params.get('end_seconds')
        if not math.isfinite(start) or (end is not None and not math.isfinite(float(end))) or start < 0 or (end is not None and float(end) <= start): raise AudioError('Trim requires start_seconds >= 0 and end_seconds > start_seconds')
        lo=min(data['frames'], round(start*rate)); hi=data['frames'] if end is None else min(data['frames'],round(float(end)*rate))
        if lo >= hi: raise AudioError('Trim selection contains no audio frames')
        y=x[lo*ch:hi*ch]; detail={'start_frame':lo,'end_frame':hi}
    elif operation == 'convert':
        new_rate=int(params.get('sample_rate_hz') or rate); new_ch=int(params.get('channels') or ch)
        if not 8000 <= new_rate <= 384000: raise AudioError('sample_rate_hz must be 8000..384000')
        if new_ch not in (1,2) or ch not in (1,2): raise AudioError('Channel mapping supports mono/stereo inputs and outputs only')
        frames=len(x)//ch
        if new_ch == 1 and ch == 2: x=[(x[i*2]+x[i*2+1])*0.5 for i in range(frames)]
        elif new_ch == 2 and ch == 1: x=[v for s in x for v in (s,s)]
        elif new_ch != ch: raise AudioError('Unsupported channel mapping')
        out_frames=max(1,round(frames*new_rate/rate)) if frames else 0
        if out_frames > MAX_FRAMES: raise AudioError('Converted output exceeds frame safety limit')
        if new_rate != rate and frames:
            y=[]
            for j in range(out_frames):
                pos=j*rate/new_rate; left=min(int(pos),frames-1); right=min(left+1,frames-1); frac=pos-left
                for c in range(new_ch): y.append(x[left*new_ch+c]*(1-frac)+x[right*new_ch+c]*frac)
        else: y=x
        rate=new_rate; ch=new_ch
        bits=int(params.get('bit_depth',24)); detail={'sample_rate_hz':rate,'channels':ch,'resampling':'linear interpolation; quality conversion should use a dedicated offline SRC for critical delivery'}
    else: raise AudioError(f'Unknown processing operation: {operation}')
    return y,ch,rate,bits,detail
