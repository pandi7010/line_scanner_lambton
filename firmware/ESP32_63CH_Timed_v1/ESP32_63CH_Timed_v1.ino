/*
  ESP32_63CH_Timed_v1 - 63-channel ejector, the ESP32 does the VALVE TIMING itself
  =============================================================================
  Made from lambton-main esp/esp.ino (that file is NOT changed - this is a new folder).
  Same wiring, same 8 x 74HC595, same baud rate (230400), same old commands.

  Wiring (as esp.ino):
    ESP32 GPIO 13 -> 74HC595 pin 14 (DATA / SER)
    ESP32 GPIO 12 -> 74HC595 pin 12 (LATCH / RCLK)
    ESP32 GPIO 14 -> 74HC595 pin 11 (CLOCK / SRCLK)
    /OE (pin 13) -> GND, /MR (pin 10) -> 5V

  WHY: on Windows the PC is often 5 - 40 ms late when it switches a valve itself.
  Now the PC sends "valve 17: ON in 6.20 ms, for 12.00 ms" the moment it finds the object,
  and this board counts the time with its own microsecond clock (micros()).

  Commands (one byte, as esp.ino):
    'P'        -> answers "ACK: ESP32_ALIVE"      (NO 30 ms LED blink any more: it would stop the timing)
    'V'        -> answers "VERSION: ESP32_TIMED_V1"
    'A'        -> all 63 ON (test)
    0 'R' 'S'  -> ALL OFF (also cancels every waiting timed pulse)
    'W' / 'X'  -> watchdog ON / OFF (250 ms auto-off for valves switched ON with 1..63)
    'T'        -> test: channels 1 - 8 ON
    1 ... 63   -> valve n ON          101 ... 163 -> valve n OFF
  NEW timed pulse (7 bytes):
    0xF0, channel, delay_lo, delay_hi, length_lo, length_hi, check
      delay  = time from receiving this command until the valve opens, in 10 us steps (max 655 ms)
      length = how long the valve stays open, in 10 us steps (max 655 ms)
      check  = channel XOR delay_lo XOR delay_hi XOR length_lo XOR length_hi
               (a damaged command is thrown away - better no blast than a wrong valve)
    Pulses for the same valve that overlap or touch are joined (long objects send
    extensions). Up to 4 separate pulses per valve can wait.

  NOT HARDWARE-TESTED: compile + upload with Arduino IDE (board "ESP32 Dev Module").
*/

#include <Arduino.h>
#include "driver/gpio.h"

constexpr uint8_t DATA_PIN = 13;
constexpr uint8_t LATCH_PIN = 12;
constexpr uint8_t CLOCK_PIN = 14;
constexpr uint8_t STATUS_LED = 2;

constexpr uint8_t NUM_ICS = 8;
constexpr uint8_t NUM_CHANNELS = 63;
constexpr uint8_t SLOTS = 4;                  // waiting pulses per valve
constexpr uint16_t WATCHDOG_MS = 250;
constexpr uint8_t CMD_TIMED = 0xF0;
constexpr uint32_t FRAME_TIMEOUT_US = 5000;   // a half-received timed command is thrown away after 5 ms

uint8_t outputBytes[NUM_ICS] = {0};
bool manualOn[NUM_CHANNELS + 1] = {false};    // switched ON with byte 1..63
uint32_t manualSince[NUM_CHANNELS + 1] = {0};
bool isOn[NUM_CHANNELS + 1] = {false};        // what the outputs show right now
bool watchdogEnabled = false;

struct Pulse {
  bool used;
  uint32_t start;     // micros()
  uint32_t end;       // micros()
};
Pulse pulses[NUM_CHANNELS + 1][SLOTS];

// timed-command receiver
uint8_t frame[6];
uint8_t frameLen = 0;
bool inFrame = false;
uint32_t frameStart = 0;
uint32_t badFrames = 0;

// time compare that also works when micros() wraps around (every 71 minutes)
inline bool reached(uint32_t now, uint32_t t) { return (int32_t)(now - t) >= 0; }

void customShiftOut(uint8_t val) {
  for (uint8_t i = 0; i < 8; ++i) {
    bool bitVal = val & (1 << (7 - i));        // MSB first, as esp.ino
    digitalWrite(DATA_PIN, bitVal ? HIGH : LOW);
    delayMicroseconds(1);
    digitalWrite(CLOCK_PIN, HIGH);
    delayMicroseconds(1);
    digitalWrite(DATA_PIN, LOW);
    digitalWrite(CLOCK_PIN, LOW);
    delayMicroseconds(1);
  }
}

void writeRegisters() {
  digitalWrite(LATCH_PIN, LOW);
  digitalWrite(CLOCK_PIN, LOW);
  delayMicroseconds(1);
  for (int8_t i = NUM_ICS - 1; i >= 0; --i) customShiftOut(outputBytes[i]);
  digitalWrite(DATA_PIN, LOW);
  digitalWrite(CLOCK_PIN, LOW);
  delayMicroseconds(1);
  digitalWrite(LATCH_PIN, HIGH);
  delayMicroseconds(2);
  digitalWrite(LATCH_PIN, LOW);
}

void setOutput(uint8_t ch, bool on) {
  const uint8_t idx = ch - 1, ic = idx / 8, bit = idx % 8;
  if (on) outputBytes[ic] |= uint8_t(1U << bit);
  else outputBytes[ic] &= uint8_t(~(1U << bit));
  isOn[ch] = on;
}

void allOff() {
  for (uint8_t ch = 1; ch <= NUM_CHANNELS; ++ch) {
    manualOn[ch] = false;
    for (uint8_t s = 0; s < SLOTS; ++s) pulses[ch][s].used = false;
    setOutput(ch, false);
  }
  writeRegisters();
  digitalWrite(STATUS_LED, LOW);
}

// add a timed pulse; join it with a waiting pulse that overlaps or touches
void addPulse(uint8_t ch, uint32_t start, uint32_t end) {
  Pulse *p = pulses[ch];
  for (uint8_t s = 0; s < SLOTS; ++s) {
    if (p[s].used && !reached(start, p[s].end + 1) && !reached(p[s].start, end + 1)) {
      if (reached(p[s].start, start)) p[s].start = start;   // earlier start
      if (reached(end, p[s].end)) p[s].end = end;           // later end
      return;
    }
  }
  for (uint8_t s = 0; s < SLOTS; ++s) {
    if (!p[s].used) { p[s] = {true, start, end}; return; }
  }
  uint8_t soonest = 0;                                      // all full: replace the one ending first
  for (uint8_t s = 1; s < SLOTS; ++s)
    if (reached(p[soonest].end, p[s].end)) soonest = s;
  p[soonest] = {true, start, end};
}

void handleTimedFrame(uint32_t now) {
  const uint8_t ch = frame[0];
  const uint8_t check = frame[0] ^ frame[1] ^ frame[2] ^ frame[3] ^ frame[4];
  if (check != frame[5] || ch < 1 || ch > NUM_CHANNELS) { badFrames++; return; }
  const uint32_t delayUs = (uint32_t(frame[1]) | (uint32_t(frame[2]) << 8)) * 10UL;
  const uint32_t lengthUs = (uint32_t(frame[3]) | (uint32_t(frame[4]) << 8)) * 10UL;
  if (lengthUs == 0) return;
  addPulse(ch, now + delayUs, now + delayUs + lengthUs);
}

void handleByte(uint8_t b, uint32_t now) {
  if (inFrame) {
    frame[frameLen++] = b;
    if (frameLen == 6) { inFrame = false; handleTimedFrame(now); }
    return;
  }
  if (b == CMD_TIMED) { inFrame = true; frameLen = 0; frameStart = now; return; }
  if (b == 'P') { Serial.println("ACK: ESP32_ALIVE"); return; }
  if (b == 'V') { Serial.println("VERSION: ESP32_TIMED_V1"); return; }
  if (b == 'A') {
    watchdogEnabled = false;
    for (uint8_t ch = 1; ch <= NUM_CHANNELS; ++ch) { manualOn[ch] = true; manualSince[ch] = millis(); }
    Serial.println("ACK: ALL 63 ON (CONTINUOUS)");
    return;
  }
  if (b == 0 || b == 'R' || b == 'S') { allOff(); Serial.println("ACK: ALL OFF"); return; }
  if (b == 'W') { watchdogEnabled = true; Serial.println("ACK: WATCHDOG ENABLED (250MS AUTO-OFF)"); return; }
  if (b == 'X') { watchdogEnabled = false; Serial.println("ACK: WATCHDOG DISABLED (CONTINUOUS HOLD)"); return; }
  if (b == 'T') {
    for (uint8_t ch = 1; ch <= 8; ++ch) { manualOn[ch] = true; manualSince[ch] = millis(); }
    Serial.println("ACK: DIAGNOSTIC TEST - IC1 (CH 01-08) ON");
    return;
  }
  if (b >= 1 && b <= NUM_CHANNELS) { manualOn[b] = true; manualSince[b] = millis(); return; }
  if (b >= 101 && b <= 100 + NUM_CHANNELS) { manualOn[b - 100] = false; return; }
  if (b != '\r' && b != '\n' && b != ' ') { Serial.print("WARN: UNKNOWN CMD "); Serial.println(b); }
}

void setup() {
  digitalWrite(DATA_PIN, LOW);
  digitalWrite(CLOCK_PIN, LOW);
  digitalWrite(LATCH_PIN, LOW);
  digitalWrite(STATUS_LED, LOW);
  pinMode(DATA_PIN, OUTPUT);
  pinMode(CLOCK_PIN, OUTPUT);
  pinMode(LATCH_PIN, OUTPUT);
  pinMode(STATUS_LED, OUTPUT);
  gpio_set_drive_capability((gpio_num_t)DATA_PIN, GPIO_DRIVE_CAP_0);
  gpio_set_drive_capability((gpio_num_t)CLOCK_PIN, GPIO_DRIVE_CAP_0);
  gpio_set_drive_capability((gpio_num_t)LATCH_PIN, GPIO_DRIVE_CAP_0);

  Serial.setRxBufferSize(1024);
  Serial.begin(230400);
  allOff();
  Serial.println("ESP32_63CH_EJECTOR_TESTER_READY");
  Serial.println("VERSION: ESP32_TIMED_V1  Pins: Data=GPIO13, Latch=GPIO12, Clock=GPIO14 | 8x 74HC595");
}

void loop() {
  // 1) read every waiting byte (the receive time counts for timed pulses)
  while (Serial.available() > 0) handleByte(uint8_t(Serial.read()), micros());

  uint32_t now = micros();
  if (inFrame && (now - frameStart) > FRAME_TIMEOUT_US) { inFrame = false; badFrames++; }

  // 2) watchdog for valves switched ON by hand (byte 1..63)
  if (watchdogEnabled) {
    const uint32_t ms = millis();
    for (uint8_t ch = 1; ch <= NUM_CHANNELS; ++ch)
      if (manualOn[ch] && uint32_t(ms - manualSince[ch]) >= WATCHDOG_MS) manualOn[ch] = false;
  }

  // 3) which valves must be open NOW? (by hand, or a timed pulse that has started and not ended)
  bool changed = false, any = false;
  for (uint8_t ch = 1; ch <= NUM_CHANNELS; ++ch) {
    bool want = manualOn[ch];
    for (uint8_t s = 0; s < SLOTS; ++s) {
      Pulse &p = pulses[ch][s];
      if (!p.used) continue;
      if (reached(now, p.end)) { p.used = false; continue; }   // pulse over
      if (reached(now, p.start)) want = true;                    // pulse running
    }
    if (want != isOn[ch]) { setOutput(ch, want); changed = true; }
    any |= want;
  }
  if (changed) {
    writeRegisters();                            // ~0.2 ms for all 64 outputs
    digitalWrite(STATUS_LED, any ? HIGH : LOW);
  }
}
