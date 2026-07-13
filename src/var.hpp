
#ifndef VAR_HPP
#define VAR_HPP

// includes

#include <string>

#include "common.hpp"
#include "libmy.hpp"

namespace var {

// variables

extern bool Ponder;
extern bool OwnBook;
extern bool SMP;
extern int  Threads;
extern int  Hash;
extern bool Chess_960;
extern Variant UCI_Variant;

// functions

void init   ();
void update ();

std::string get (const std::string & name);
void        set (const std::string & name, const std::string & value);

bool get_bool (const std::string & name);
int  get_int  (const std::string & name);

bool        variant_is_ok      (const std::string & value);
Variant     variant_from_string (const std::string & value);
std::string variant_to_string   (Variant value);

}

#endif // !defined VAR_HPP

