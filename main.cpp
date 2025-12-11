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
	IPLAudioSettings audioSettings{};
	IPLAudioBuffer outBuffer{};
	std::vector<float> outputaudioframe;
	std::vector<int16_t> outputInt16;
	
	// Reverb state
	IPLReflectionEffect reflectionEffect = nullptr;
	IPLAudioBuffer reverbInBuffer{};
	IPLAudioBuffer reverbOutBuffer{};
	std::vector<float> reverbInputData;
	std::vector<float> reverbOutputData;
	
	// Reverb settings
	float roomSize = 0.5f;
	float damping = 0.5f;
	float wet = 1.0f;
	float dry = 0.0f;
	float width = 1.0f;
	
	bool initialized = false;
	bool reverbInitialized = false;
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

	// Initialize Reflection Effect for Reverb (Parametric)
	IPLReflectionEffectSettings reflectionSettings{};
	reflectionSettings.type = IPL_REFLECTIONEFFECTTYPE_PARAMETRIC;
	reflectionSettings.irSize = 0; // Not used for parametric? Or maybe needs to be non-zero? 
	// To be safe for parametric, we might need a non-zero irSize or it might be ignored. 
	// However, usually irSize is for convolution. Let's assume 0 is fine or set a small default.
	// Actually, for Hybrid/Parametric, Steam Audio might use internal buffers.
    // Let's set it to 2 seconds worth just in case it's needed for internal buffers.
	reflectionSettings.irSize = samplingrate * 2; 
	reflectionSettings.numChannels = 2; // Stereo reverb

	if (iplReflectionEffectCreate(g_state.context, &g_state.audioSettings, &reflectionSettings, &g_state.reflectionEffect) != IPL_STATUS_SUCCESS) {
		// If parametric fails, we might try convolution or just fail. 
		// But let's assume it works as it's standard 4.0+.
		iplAudioBufferFree(g_state.context, &g_state.outBuffer);
		iplBinauralEffectRelease(&g_state.effect);
		iplHRTFRelease(&g_state.hrtf);
		iplContextRelease(&g_state.context);
		return false;
	}

	// Allocate buffers for reverb processing
	if (iplAudioBufferAllocate(g_state.context, 2, g_state.audioSettings.frameSize, &g_state.reverbInBuffer) != IPL_STATUS_SUCCESS ||
		iplAudioBufferAllocate(g_state.context, 2, g_state.audioSettings.frameSize, &g_state.reverbOutBuffer) != IPL_STATUS_SUCCESS) {
		iplReflectionEffectRelease(&g_state.reflectionEffect);
		iplAudioBufferFree(g_state.context, &g_state.outBuffer);
		iplBinauralEffectRelease(&g_state.effect);
		iplHRTFRelease(&g_state.hrtf);
		iplContextRelease(&g_state.context);
		return false;
	}

	g_state.reverbInitialized = true;
	g_state.outputaudioframe.resize(2 * framesize);
	g_state.outputInt16.resize(2 * framesize);
	
	// Pre-allocate temp buffers for interleaved conversion if needed, 
	// though iplAudioBufferAllocate gives us deinterleaved buffers.
	
	g_state.initialized = true;
	return true;
}

EXPORT void cleanup_steam_audio()
{
	if (!g_state.initialized) {
		return;
	}

	if (g_state.reverbInitialized) {
		iplAudioBufferFree(g_state.context, &g_state.reverbOutBuffer);
		iplAudioBufferFree(g_state.context, &g_state.reverbInBuffer);
		iplReflectionEffectRelease(&g_state.reflectionEffect);
	}

	iplAudioBufferFree(g_state.context, &g_state.outBuffer);
	iplBinauralEffectRelease(&g_state.effect);
	iplHRTFRelease(&g_state.hrtf);
	iplContextRelease(&g_state.context);

	g_state = SteamAudioState{}; // Reset to default state
}

EXPORT bool set_reverb_settings(float room_size, float damping, float wet_level, float dry_level, float width)
{
	if (!g_state.initialized || !g_state.reverbInitialized) {
		return false;
	}

	g_state.roomSize = std::max(0.0f, std::min(1.0f, room_size));
	g_state.damping = std::max(0.0f, std::min(1.0f, damping));
	g_state.wet = std::max(0.0f, wet_level);
	g_state.dry = std::max(0.0f, dry_level);
	g_state.width = std::max(0.0f, width);

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

		if (iplBinauralEffectApply(g_state.effect, &params, &inBuffer, &g_state.outBuffer) != IPL_STATUS_SUCCESS) {
			delete[] output;
			return false;
		}

		iplAudioBufferInterleave(g_state.context, &g_state.outBuffer, g_state.outputaudioframe.data());

		// Convert float samples to 16-bit integers
		for (int j = 0; j < framesize * 2; ++j) {
			float sample = g_state.outputaudioframe[j];
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

EXPORT bool apply_reverb(const int16_t* input_buffer, int input_length, int16_t** output_buffer, int* output_length)
{
	if (!g_state.initialized || !g_state.reverbInitialized || !input_buffer || !output_buffer || !output_length) {
		return false;
	}

	auto framesize = g_state.audioSettings.frameSize;
	auto numframes = (input_length / 2 + framesize - 1) / framesize; // Ceiling division, /2 because stereo

	if (numframes == 0) {
		*output_buffer = nullptr;
		*output_length = 0;
		return true;
	}

	// Calculate tail frames for reverb decay
	// Map roomSize (0-1) to decay time (e.g., 0.1s to 5.0s)
	float decayTime = 0.1f + g_state.roomSize * 4.9f;
	
	// Convert decay time to frames
	unsigned long tail_frames_count = static_cast<unsigned long>((decayTime * g_state.audioSettings.samplingRate));
	auto tail_blocks = (tail_frames_count + framesize - 1) / framesize;

	auto total_frames = numframes + tail_blocks;
	auto total_output_samples = total_frames * framesize * 2; // 2 channels

	// Allocate output buffer
	int16_t* output = new int16_t[total_output_samples];

	// Prepare padded input data
	std::vector<float> paddedInput(total_frames * framesize * 2, 0.0f);

	// Convert input int16 to float and copy to padded buffer
	for (int i = 0; i < input_length; ++i) {
		paddedInput[i] = static_cast<float>(input_buffer[i]) / 32767.0f;
	}

	int16_t* outData = output;
	const float* inData = paddedInput.data();

	// Calculate reverb parameters once
	IPLReflectionEffectParams params = {};
	params.type = IPL_REFLECTIONEFFECTTYPE_PARAMETRIC;
	params.numChannels = 2;
	params.irSize = 0; // Not used for apply
	params.ir = nullptr;

	// Set decay times for 3 bands (Low, Mid, High)
	// Simple mapping: High freq decay is reduced by damping
	params.reverbTimes[0] = decayTime;
	params.reverbTimes[1] = decayTime;
	params.reverbTimes[2] = decayTime * (1.0f - g_state.damping * 0.8f); // Max 80% reduction
	
	// Default EQ
	params.eq[0] = 1.0f;
	params.eq[1] = 1.0f;
	params.eq[2] = 1.0f;

    // Steam Audio buffers are deinterleaved.
	// We need to deinterleave 'inData' into 'g_state.reverbInBuffer'.
	// g_state.reverbInBuffer.data[0] is Left, data[1] is Right.

	for (int i = 0; i < total_frames; ++i)
	{
		// Deinterleave input
		for (int j = 0; j < framesize; ++j) {
			g_state.reverbInBuffer.data[0][j] = inData[j * 2];     // L
			g_state.reverbInBuffer.data[1][j] = inData[j * 2 + 1]; // R
		}

		// Apply reverb
		iplReflectionEffectApply(g_state.reflectionEffect, &params, &g_state.reverbInBuffer, &g_state.reverbOutBuffer, nullptr);

		// Interleave and mix
		// Output = Dry * Input + Wet * Reverb
		// Also apply simple Width (Mid/Side processing) if needed, but for now simple mix.
		
		float wet = g_state.wet;
		float dry = g_state.dry;
		float width = g_state.width;
		
		for (int j = 0; j < framesize; ++j) {
			float inL = g_state.reverbInBuffer.data[0][j];
			float inR = g_state.reverbInBuffer.data[1][j];
			float revL = g_state.reverbOutBuffer.data[0][j];
			float revR = g_state.reverbOutBuffer.data[1][j];
			
			// Mix
			float outL = (inL * dry) + (revL * wet);
			float outR = (inR * dry) + (revR * wet);
			
			// Apply Width (Simple M/S)
			if (width != 1.0f) {
				float mid = (outL + outR) * 0.5f;
				float side = (outL - outR) * 0.5f;
				side *= width;
				outL = mid + side;
				outR = mid - side;
			}
			
			// Clamp and convert
			outL = std::max(-1.0f, std::min(1.0f, outL));
			outR = std::max(-1.0f, std::min(1.0f, outR));
			
			outData[j * 2] = static_cast<int16_t>(outL * 32767.0f);
			outData[j * 2 + 1] = static_cast<int16_t>(outR * 32767.0f);
		}

		inData += framesize * 2;
		outData += framesize * 2;
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
