#include <algorithm>
#include <vector>
#include <memory>

#define _USE_MATH_DEFINES
#include <cmath>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#include <phonon.h>

#ifdef _WIN32
#define EXPORT extern "C" __declspec(dllexport)
#else
#define EXPORT extern "C"
#endif

// Global state for Steam Audio
struct SteamAudioState {
    IPLContext context = nullptr;
    IPLHRTF hrtf = nullptr;
    IPLBinauralEffect effect = nullptr;
    IPLReflectionEffect reflectionEffect = nullptr;
    IPLAudioSettings audioSettings{};
    IPLAudioBuffer outBuffer{};
    IPLAudioBuffer reflectionBuffer{};
    std::vector<float> outputaudioframe;
    std::vector<int16_t> outputInt16;
    bool initialized = false;
    bool reverbEnabled = false;
    float reverbLevel = 1.0f;
    float reverbTime = 0.2f;
};

static SteamAudioState g_state;

EXPORT bool initialize_steam_audio(int samplingrate, int framesize)
{
    if (g_state.initialized) {
        return true; // Already initialized
    }

    IPLContextSettings contextSettings{};
    contextSettings.version = STEAMAUDIO_VERSION;

    if (iplContextCreate(&contextSettings, &g_state.context) != IPL_STATUS_SUCCESS) {
        return false;
    }

    g_state.audioSettings = { samplingrate, framesize };

    IPLHRTFSettings hrtfSettings;
    hrtfSettings.type = IPL_HRTFTYPE_DEFAULT;
    hrtfSettings.volume = 1.0f;

    if (iplHRTFCreate(g_state.context, &g_state.audioSettings, &hrtfSettings, &g_state.hrtf) != IPL_STATUS_SUCCESS) {
        iplContextRelease(&g_state.context);
        return false;
    }

    IPLBinauralEffectSettings effectSettings;
    effectSettings.hrtf = g_state.hrtf;

    if (iplBinauralEffectCreate(g_state.context, &g_state.audioSettings, &effectSettings, &g_state.effect) != IPL_STATUS_SUCCESS) {
        iplHRTFRelease(&g_state.hrtf);
        iplContextRelease(&g_state.context);
        return false;
    }

    if (iplAudioBufferAllocate(g_state.context, 2, g_state.audioSettings.frameSize, &g_state.outBuffer) != IPL_STATUS_SUCCESS) {
        iplBinauralEffectRelease(&g_state.effect);
        iplHRTFRelease(&g_state.hrtf);
        iplContextRelease(&g_state.context);
        return false;
    }

    // Initialize reflection effect for reverb
    IPLReflectionEffectSettings reflectionSettings{};
    reflectionSettings.type = IPL_REFLECTIONEFFECTTYPE_PARAMETRIC;
    reflectionSettings.irSize = g_state.audioSettings.frameSize * 4; // IR size in samples
    reflectionSettings.numChannels = 2; // Stereo output
    
    if (iplReflectionEffectCreate(g_state.context, &g_state.audioSettings, &reflectionSettings, &g_state.reflectionEffect) != IPL_STATUS_SUCCESS) {
        iplAudioBufferFree(g_state.context, &g_state.outBuffer);
        iplBinauralEffectRelease(&g_state.effect);
        iplHRTFRelease(&g_state.hrtf);
        iplContextRelease(&g_state.context);
        return false;
    }

    if (iplAudioBufferAllocate(g_state.context, 2, g_state.audioSettings.frameSize, &g_state.reflectionBuffer) != IPL_STATUS_SUCCESS) {
        iplReflectionEffectRelease(&g_state.reflectionEffect);
        iplAudioBufferFree(g_state.context, &g_state.outBuffer);
        iplBinauralEffectRelease(&g_state.effect);
        iplHRTFRelease(&g_state.hrtf);
        iplContextRelease(&g_state.context);
        return false;
    }

    g_state.outputaudioframe.resize(2 * framesize);
    g_state.outputInt16.resize(2 * framesize);
    g_state.initialized = true;
    return true;
}

EXPORT void cleanup_steam_audio()
{
    if (!g_state.initialized) {
        return;
    }

    iplAudioBufferFree(g_state.context, &g_state.reflectionBuffer);
    iplReflectionEffectRelease(&g_state.reflectionEffect);
    iplAudioBufferFree(g_state.context, &g_state.outBuffer);
    iplBinauralEffectRelease(&g_state.effect);
    iplHRTFRelease(&g_state.hrtf);
    iplContextRelease(&g_state.context);
    
    g_state = SteamAudioState{}; // Reset to default state
}

EXPORT bool load_sound(const float* buffer, int length)
{
    if (!g_state.initialized) {
        return false;
    }
    
    // For this simple implementation, we don't need to store the input buffer
    // as it will be passed directly to process_sound
    return true;
}

EXPORT bool set_reverb_settings(bool enabled, float level, float time)
{
    if (!g_state.initialized) {
        return false;
    }
    // Reverb is broken, disable it for now.
        enabled = false;
        
    g_state.reverbEnabled = enabled;
    g_state.reverbLevel = level;
    g_state.reverbTime = time;
    return true;
}

EXPORT bool process_sound(const float* input_buffer, int input_length, float angle_x, float angle_y, int16_t** output_buffer, int* output_length)
{
    if (!g_state.initialized || !input_buffer || !output_buffer || !output_length) {
        return false;
    }

    auto framesize = g_state.audioSettings.frameSize;
    auto numframes = (input_length + framesize - 1) / framesize; // Ceiling division
    
    if (numframes == 0) {
        *output_buffer = nullptr;
        *output_length = 0;
        return true;
    }

    // Allocate output buffer for stereo output (16-bit samples)
    auto total_output_samples = numframes * framesize * 2; // 2 channels
    int16_t* output = new int16_t[total_output_samples];
    
    // Create a padded input buffer to handle partial frames
    std::vector<float> paddedInput;
    const float* inData = input_buffer;
    if (input_length % framesize != 0) {
        paddedInput.resize(numframes * framesize, 0.0f);
        std::copy(input_buffer, input_buffer + input_length, paddedInput.begin());
        inData = paddedInput.data();
    }
    
    int16_t* outData = output;

    // Treat input as Cartesian coordinates (x, y) and create normalized direction vector
    // Steam Audio uses right-handed coordinate system: +X right, +Y up, +Z forward
    IPLVector3 direction;
    direction.x = angle_x;  // X coordinate (left/right)
    direction.y = angle_y;  // Y coordinate (up/down) 
    direction.z = 1.0f;     // Default forward distance
    
    // Normalize the direction vector
    float length = sqrtf(direction.x * direction.x + direction.y * direction.y + direction.z * direction.z);
    if (length > 0.0f) {
        direction.x /= length;
        direction.y /= length;
        direction.z /= length;
    } else {
        // Default to forward direction if coordinates are zero
        direction.x = 0.0f;
        direction.y = 0.0f;
        direction.z = 1.0f;
    }

    for (int i = 0; i < numframes; ++i)
    {
        float* frameData[] = { const_cast<float*>(inData) };
        IPLAudioBuffer inBuffer{ 1, framesize, frameData };

        IPLBinauralEffectParams params;
        params.direction = direction;
        params.interpolation = IPL_HRTFINTERPOLATION_NEAREST;
        params.spatialBlend = 1.0f;
        params.hrtf = g_state.hrtf;
        params.peakDelays = nullptr;

        IPLAudioBuffer* finalBuffer = &g_state.outBuffer;

        if (iplBinauralEffectApply(g_state.effect, &params, &inBuffer, &g_state.outBuffer) != IPL_STATUS_SUCCESS) {
            delete[] output;
            return false;
        }

        // Apply reflection/reverb if enabled
        if (g_state.reverbEnabled) {
            IPLReflectionEffectParams reflectionParams;
            reflectionParams.reverbTimes[0] = g_state.reverbTime;
            reflectionParams.reverbTimes[1] = g_state.reverbTime;
            reflectionParams.reverbTimes[2] = g_state.reverbTime;
            reflectionParams.eq[0] = 1.0f;
            reflectionParams.eq[1] = 1.0f;
            reflectionParams.eq[2] = 1.0f;
            reflectionParams.delay = 0.0f;
            reflectionParams.numChannels = 2;
            reflectionParams.irSize = g_state.audioSettings.frameSize * 4;
            reflectionParams.ir = nullptr; // Use parametric reverb, no IR needed

            if (iplReflectionEffectApply(g_state.reflectionEffect, &reflectionParams, &g_state.outBuffer, &g_state.reflectionBuffer, nullptr) == IPL_STATUS_SUCCESS) {
                finalBuffer = &g_state.reflectionBuffer;
            }
        }

        iplAudioBufferInterleave(g_state.context, finalBuffer, g_state.outputaudioframe.data());

        // Convert float samples to 16-bit integers
        for (int j = 0; j < framesize * 2; ++j) {
            float sample = g_state.outputaudioframe[j] * g_state.reverbLevel;
            // Clamp to prevent overflow
            sample = std::max(-1.0f, std::min(1.0f, sample));
            g_state.outputInt16[j] = static_cast<int16_t>(sample * 32767.0f);
        }

        // Copy 16-bit stereo data to output buffer
        std::copy(g_state.outputInt16.begin(), g_state.outputInt16.end(), outData);
        
        inData += framesize;
        outData += framesize * 2; // 2 channels
    }

    *output_buffer = output;
    *output_length = total_output_samples;
    return true;
}

EXPORT void free_output_sound(int16_t* buffer)
{
    if (buffer) {
        delete[] buffer;
    }
}
