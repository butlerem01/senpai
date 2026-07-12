
// includes

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <map>
#include <string>

#include "libmy.hpp"
#include "var.hpp"

namespace var {

// variables

bool Ponder;
bool SMP;
int  Threads;
int  Hash;
bool Chess_960;
Variant UCI_Variant;

static std::map<std::string, std::string> Var;

// functions

void init() {

   set("Ponder", "false");
   set("Threads", "1");
   set("Hash", "64");
   set("UCI_Chess960", "false");
   set("UCI_Variant", "chess");

   update();
}

void update() {

   Ponder    = get_bool("Ponder");
   Threads   = get_int("Threads");
   SMP       = Threads > 1;
   Hash      = 1 << ml::log_2(get_int("Hash"));
   Chess_960 = get_bool("UCI_Chess960");
   UCI_Variant = variant_from_string(get("UCI_Variant"));

   variant_set(UCI_Variant);
}

std::string get(const std::string & name) {

   if (Var.find(name) == Var.end()) {
      std::cerr << "unknown variable: \"" << name << "\"" << std::endl;
      std::exit(EXIT_FAILURE);
   }

   return Var[name];
}

void set(const std::string & name, const std::string & value) {
   Var[name] = value;
}

bool get_bool(const std::string & name) {

   std::string value = get(name);

   if (value == "true") {
      return true;
   } else if (value == "false") {
      return false;
   } else {
      std::cerr << "not a boolean: variable " << name << " = \"" << value << "\"" << std::endl;
      std::exit(EXIT_FAILURE);
      return false;
   }
}

int get_int(const std::string & name) {
   return std::stoi(get(name));
}

bool variant_is_ok(const std::string & value) {
   return value == "chess" || value == "omega";
}

Variant variant_from_string(const std::string & value) {

   if (value == "chess") return Chess;
   if (value == "omega") return Omega;

   std::cerr << "unknown UCI variant: \"" << value << "\"" << std::endl;
   std::exit(EXIT_FAILURE);
}

std::string variant_to_string(Variant value) {

   if (value == Chess) return "chess";
   if (value == Omega) return "omega";

   std::cerr << "invalid UCI variant" << std::endl;
   std::exit(EXIT_FAILURE);
}

}

