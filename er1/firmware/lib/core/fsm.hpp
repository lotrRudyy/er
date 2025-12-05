#pragma once
#include <Arduino.h>
namespace FSM {
  struct State { virtual const char* name() const = 0; virtual void onEnter(){}; virtual void onTick(){}; virtual void onExit(){}; virtual ~State(){}; };
  class Machine { State* _cur=nullptr; public: void set(State* s){ if(_cur) _cur->onExit(); _cur=s; if(_cur) _cur->onEnter(); } void loop(){ if(_cur) _cur->onTick(); } const char* name() const { return _cur? _cur->name() : "NONE"; } };
}