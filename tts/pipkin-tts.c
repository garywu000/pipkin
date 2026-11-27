/*
 * pipkin-tts: keeps one Kokoro voice model loaded (through the sherpa-onnx C API) and turns lines of text into
 * WAV files, so Pipkin can speak each sentence without reloading the model.
 *
 * Usage: pipkin-tts --model M --voices V --tokens T --lexicon L [--threads N]
 * Protocol (stdin/stdout, one request per line, tab-separated):
 *   request:  <speaker id>\t<speed>\t<output .wav path>\t<text>\n
 *   reply:    ok <samples> <sample rate>\n   or   error <message>\n
 * On startup it prints "ready <sample rate> <speakers>\n", or "error ...\n" and exits.
 * It reads nothing else, opens no network connections, and writes only the paths it is given.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "sherpa-onnx/c-api/c-api.h"

#define MAX_LINE (64 * 1024)

static const char *arg(int argc, char **argv, const char *name, const char *fallback) {
  for (int i = 1; i + 1 < argc; i++) {
    if (strcmp(argv[i], name) == 0) return argv[i + 1];
  }
  return fallback;
}

static char *next_tab(char **cursor) {
  if (!cursor || !*cursor) return NULL;
  char *start = *cursor;
  char *tab = strchr(start, '\t');
  if (!tab) {
    *cursor = NULL;
    return start;
  }
  *tab = '\0';
  *cursor = tab + 1;
  return start;
}

int main(int argc, char **argv) {
  const char *model = arg(argc, argv, "--model", NULL);
  const char *voices = arg(argc, argv, "--voices", NULL);
  const char *tokens = arg(argc, argv, "--tokens", NULL);
  const char *lexicon = arg(argc, argv, "--lexicon", NULL);
  int threads = atoi(arg(argc, argv, "--threads", "2"));
  if (!model || !voices || !tokens || !lexicon) {
    printf("error missing model paths\n");
    return 2;
  }
  if (threads < 1 || threads > 8) threads = 2;

  SherpaOnnxOfflineTtsConfig config;
  memset(&config, 0, sizeof(config));
  config.model.kokoro.model = model;
  config.model.kokoro.voices = voices;
  config.model.kokoro.tokens = tokens;
  config.model.kokoro.lexicon = lexicon;
  config.model.kokoro.length_scale = 1.0f;
  config.model.num_threads = threads;
  config.model.provider = "cpu";
  config.max_num_sentences = 1;

  const SherpaOnnxOfflineTts *tts = SherpaOnnxCreateOfflineTts(&config);
  if (!tts) {
    printf("error could not load the voice model\n");
    return 3;
  }
  printf("ready %d %d\n", SherpaOnnxOfflineTtsSampleRate(tts), SherpaOnnxOfflineTtsNumSpeakers(tts));
  fflush(stdout);

  int speakers = SherpaOnnxOfflineTtsNumSpeakers(tts);
  char *line = malloc(MAX_LINE);
  while (line && fgets(line, MAX_LINE, stdin)) {
    size_t len = strlen(line);
    while (len && (line[len - 1] == '\n' || line[len - 1] == '\r')) line[--len] = '\0';
    if (!len) continue;
    char *cursor = line;
    char *sid_text = next_tab(&cursor);
    char *speed_text = next_tab(&cursor);
    char *path = next_tab(&cursor);
    char *text = cursor;
    if (!sid_text || !*sid_text || !speed_text || !*speed_text || !path || !*path || !text || !*text) {
      printf("error bad request\n");
      fflush(stdout);
      continue;
    }
    int sid = atoi(sid_text);
    float speed = (float)atof(speed_text);
    if (sid < 0 || sid >= speakers) sid = 0;
    if (speed < 0.5f || speed > 2.0f) speed = 1.0f;
    SherpaOnnxGenerationConfig generation;
    memset(&generation, 0, sizeof(generation));
    generation.sid = sid;
    generation.speed = speed;
    generation.silence_scale = 0.2f;
    const SherpaOnnxGeneratedAudio *audio = SherpaOnnxOfflineTtsGenerateWithConfig(tts, text, &generation, NULL, NULL);
    if (!audio || audio->n <= 0) {
      printf("error no audio\n");
    } else if (!SherpaOnnxWriteWave(audio->samples, audio->n, audio->sample_rate, path)) {
      printf("error could not write audio\n");
    } else {
      printf("ok %d %d\n", audio->n, audio->sample_rate);
    }
    if (audio) SherpaOnnxDestroyOfflineTtsGeneratedAudio(audio);
    fflush(stdout);
  }
  free(line);
  SherpaOnnxDestroyOfflineTts(tts);
  return 0;
}
