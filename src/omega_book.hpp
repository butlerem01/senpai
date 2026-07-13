#ifndef OMEGA_BOOK_HPP
#define OMEGA_BOOK_HPP

#include <cstddef>
#include <memory>
#include <string>

#include "common.hpp"

class List;
class Pos;

namespace omega_book {

struct Configure_Result {
   bool ok { false };
   bool disabled { false };
   bool retained_previous { false };
   std::string message;
};

// OmegaBookFile is parsed without changing the process-wide board geometry.
// A replacement is published atomically only after every record passes the
// format checks.  Position-specific legality is checked against the legal
// root list when the book is probed.
class Runtime_Book {
public:
   Runtime_Book();

   Configure_Result configure(const std::string & file_name);
   bool probe(const Pos & pos, const List & legal, Move & move) const;

   bool loaded() const;
   std::string path() const;

private:
   struct Book_Data;
   std::shared_ptr<const Book_Data> p_book;
};

extern Runtime_Book G_Book;

} // namespace omega_book

#endif
