// Host shim: ArduinoJson only defines DeserializationError::f_str() with PROGMEM.
#pragma once
#include "Arduino.h"
#include <ArduinoJson.h>
#define f_str() c_str()
