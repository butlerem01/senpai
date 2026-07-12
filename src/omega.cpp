// Omega Chess companion engine for Senpai 2.0.
// GPL-3.0-or-later, matching Senpai's licence.
#include <algorithm>
#include <chrono>
#include <cctype>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <sstream>
#include <string>
#include <vector>

namespace omega {

enum Piece { Empty, Pawn, Knight, Bishop, Rook, Queen, King, Champion, Wizard };
struct Cell { Piece piece; bool white; Cell() : piece(Empty), white(false) {} };
struct Move { int from, to; Piece promotion; Move(int f=-1,int t=-1,Piece p=Empty):from(f),to(t),promotion(p){} };

static const char * Start = "crnbqkbnrc/pppppppppp/10/10/10/10/10/10/PPPPPPPPPP/CRNBQKBNRC[W/W/w/w] w KQkq - 0 1";

class Position {
public:
   Cell board[104];
   bool white_to_move;
   std::string castling;
   std::vector<int> ep;
   int halfmove, fullmove;

   Position() : white_to_move(true), castling("-"), halfmove(0), fullmove(1) {
      for (int i = 0; i < 104; i++) board[i] = Cell();
      if (!load(Start)) std::abort();
   }

   static bool corner(int sq) { return sq >= 100 && sq < 104; }
   static int square(int file, int rank) {
      if (file >= 0 && file < 10 && rank >= 0 && rank < 10) return rank * 10 + file;
      if (file == -1 && rank == -1) return 100;
      if (file == 10 && rank == -1) return 101;
      if (file == 10 && rank == 10) return 102;
      if (file == -1 && rank == 10) return 103;
      return -1;
   }
   static void coordinates(int sq, int & file, int & rank) {
      if (sq < 100) { file = sq % 10; rank = sq / 10; return; }
      static const int cf[4] = {-1,10,10,-1};
      static const int cr[4] = {-1,-1,10,10};
      file=cf[sq-100]; rank=cr[sq-100];
   }
   static int parse_square(const std::string & text) {
      if (text.size()!=2) return -1;
      if ((text[0]=='w'||text[0]=='W') && text[1]>='1'&&text[1]<='4') return 100+text[1]-'1';
      if (text[0]>='a'&&text[0]<='j'&&text[1]>='0'&&text[1]<='9') return square(text[0]-'a',text[1]-'0');
      return -1;
   }
   static std::string square_name(int sq) {
      if (corner(sq)) return std::string("w")+char('1'+sq-100);
      std::string out; out+=char('a'+sq%10); out+=char('0'+sq/10); return out;
   }
   static Piece decode(char c) {
      switch(std::tolower(static_cast<unsigned char>(c))) {
         case 'p':return Pawn; case 'n':return Knight; case 'b':return Bishop;
         case 'r':return Rook; case 'q':return Queen; case 'k':return King;
         case 'c':return Champion; case 'w':return Wizard; default:return Empty;
      }
   }
   static char encode(Piece p) {
      static const char chars[]=" pnbrqkcw"; return chars[int(p)];
   }

   bool load(const std::string & fen) {
      Position previous=*this;
      if(load_unchecked(fen))return true;
      *this=previous;
      return false;
   }
   bool load_unchecked(const std::string & fen) {
      for(int i=0;i<104;i++) board[i]=Cell(); ep.clear();
      std::stringstream ss(fen); std::string placement,turn,eps;
      if(!(ss>>placement>>turn>>castling>>eps>>halfmove>>fullmove)) return false;
      std::size_t open=placement.find('['), close=placement.find(']');
      if(open==std::string::npos||close==std::string::npos||close!=placement.size()-1) return false;
      std::stringstream ranks(placement.substr(0,open)); std::string row;
      for(int rank=9;rank>=0;rank--) {
         if(!std::getline(ranks,row,'/')) return false; int file=0;
         for(std::size_t i=0;i<row.size();) {
            if(std::isdigit(static_cast<unsigned char>(row[i]))) {
               int count=0; while(i<row.size()&&std::isdigit(static_cast<unsigned char>(row[i]))) count=count*10+row[i++]-'0';
               file+=count;
            } else {
               if(file>=10) return false; char c=row[i++]; Piece p=decode(c); if(p==Empty)return false;
               board[square(file++,rank)].piece=p; board[square(file-1,rank)].white=std::isupper(static_cast<unsigned char>(c));
            }
         }
         if(file!=10)return false;
      }
      if(std::getline(ranks,row,'/'))return false;
      std::stringstream corners(placement.substr(open+1,close-open-1));
      for(int i=0;i<4;i++) { std::string token; if(!std::getline(corners,token,'/'))return false;
         if(token!="-") { if(token.size()!=1)return false; Piece piece=decode(token[0]);if(piece==Empty)return false;board[100+i].piece=piece; board[100+i].white=std::isupper(static_cast<unsigned char>(token[0])); }
      }
      std::string extra;
      if(std::getline(corners,extra,'/'))return false;
      if(turn!="w"&&turn!="b")return false;
      white_to_move=turn=="w";
      if(castling!="-"){
         bool seen[4]={false,false,false,false};
         for(std::size_t i=0;i<castling.size();i++){
            std::size_t index=std::string("KQkq").find(castling[i]);
            if(index==std::string::npos||seen[index])return false;
            seen[index]=true;
         }
      }
      if(eps!="-") { std::stringstream es(eps); std::string token; while(std::getline(es,token,',')) { int sq=parse_square(token); if(sq<0)return false; ep.push_back(sq); } }
      if(halfmove<0||fullmove<1||(ss>>extra))return false;

      int white_kings=0,black_kings=0;
      for(int sq=0;sq<104;sq++)if(board[sq].piece==King)(board[sq].white?white_kings:black_kings)++;
      if(white_kings!=1||black_kings!=1)return false;

      return true;
   }

   std::string ofen() const {
      std::ostringstream out;
      for(int rank=9;rank>=0;rank--){
         if(rank!=9)out<<'/';int empty=0;
         for(int file=0;file<10;file++){
            const Cell&cell=board[square(file,rank)];
            if(cell.piece==Empty){empty++;continue;}
            if(empty!=0){out<<empty;empty=0;}
            char piece=encode(cell.piece);
            out<<(cell.white?char(std::toupper(static_cast<unsigned char>(piece))):piece);
         }
         if(empty!=0)out<<empty;
      }
      out<<'[';
      for(int corner=0;corner<4;corner++){
         if(corner!=0)out<<'/';const Cell&cell=board[100+corner];
         if(cell.piece==Empty){out<<'-';continue;}
         char piece=encode(cell.piece);
         out<<(cell.white?char(std::toupper(static_cast<unsigned char>(piece))):piece);
      }
      out<<"] "<<(white_to_move?'w':'b')<<' '<<(castling.empty()||castling=="-"?"-":castling)<<' ';
      if(ep.empty())out<<'-';
      for(std::size_t i=0;i<ep.size();i++){if(i!=0)out<<',';out<<square_name(ep[i]);}
      out<<' '<<halfmove<<' '<<fullmove;
      return out.str();
   }

   void add(std::vector<Move>& out,int from,int to,Piece promotion=Empty) const {
      if(to<0||to>=104)return; if(board[to].piece!=Empty&&board[to].white==board[from].white)return;
      out.push_back(Move(from,to,promotion));
   }
   void leaps(std::vector<Move>&out,int from,const int offsets[][2],int count) const {
      int f,r;coordinates(from,f,r); for(int i=0;i<count;i++)add(out,from,square(f+offsets[i][0],r+offsets[i][1]));
   }
   void slides(std::vector<Move>&out,int from,const int dirs[][2],int count) const {
      if(corner(from))return; int f,r;coordinates(from,f,r);
      for(int i=0;i<count;i++)for(int n=1;;n++){int to=square(f+dirs[i][0]*n,r+dirs[i][1]*n);if(to<0)break;add(out,from,to);if(corner(to)||board[to].piece!=Empty)break;}
   }
   bool can_castle(bool king_side) const {
      int rank=white_to_move?0:9, king_from=square(5,rank), king_to=square(king_side?7:3,rank);
      int rook_from=square(king_side?8:1,rank);
      char right=white_to_move?(king_side?'K':'Q'):(king_side?'k':'q');
      if(castling.find(right)==std::string::npos||board[king_from].piece!=King||board[rook_from].piece!=Rook)return false;
      int step=king_side?1:-1;for(int f=5+step;f!=(king_side?8:1);f+=step)if(board[square(f,rank)].piece!=Empty)return false;
      for(int f=5;f!=(king_side?8:2);f+=step)if(attacked(square(f,rank),!white_to_move))return false;
      return board[king_to].piece==Empty;
   }
   std::vector<Move> pseudo(bool include_castling=true) const {
      static const int diag[][2]={{1,1},{1,-1},{-1,1},{-1,-1}};
      static const int orth[][2]={{1,0},{-1,0},{0,1},{0,-1}};
      static const int knight[][2]={{1,2},{2,1},{-1,2},{-2,1},{1,-2},{2,-1},{-1,-2},{-2,-1}};
      static const int champion[][2]={{1,0},{-1,0},{0,1},{0,-1},{2,0},{-2,0},{0,2},{0,-2},{2,2},{2,-2},{-2,2},{-2,-2}};
      static const int wizard[][2]={{1,1},{1,-1},{-1,1},{-1,-1},{1,3},{1,-3},{-1,3},{-1,-3},{3,1},{3,-1},{-3,1},{-3,-1}};
      std::vector<Move> out;
      for(int from=0;from<104;from++)if(board[from].piece!=Empty&&board[from].white==white_to_move){
         switch(board[from].piece){
            case Knight:leaps(out,from,knight,8);break; case Champion:leaps(out,from,champion,12);break; case Wizard:leaps(out,from,wizard,12);break;
            case Bishop:slides(out,from,diag,4);break; case Rook:slides(out,from,orth,4);break;
            case Queen:slides(out,from,diag,4);slides(out,from,orth,4);break;
            case King:leaps(out,from,diag,4);leaps(out,from,orth,4);if(include_castling&&from==square(5,white_to_move?0:9)){if(can_castle(true))add(out,from,square(7,white_to_move?0:9));if(can_castle(false))add(out,from,square(3,white_to_move?0:9));}break;
            case Pawn:{if(corner(from))break;int f,r;coordinates(from,f,r);int d=board[from].white?1:-1;int max=(r==(board[from].white?1:8))?3:1;
               for(int n=1;n<=max;n++){int to=square(f,r+d*n);if(to<0||board[to].piece!=Empty)break;if(r+d*n==0||r+d*n==9){add(out,from,to,Queen);add(out,from,to,Rook);add(out,from,to,Bishop);add(out,from,to,Knight);add(out,from,to,Champion);add(out,from,to,Wizard);}else add(out,from,to);}
               for(int dx=-1;dx<=1;dx+=2){int to=square(f+dx,r+d);if(to>=0&&((board[to].piece!=Empty&&board[to].white!=board[from].white)||std::find(ep.begin(),ep.end(),to)!=ep.end()))add(out,from,to,(r+d==0||r+d==9)?Queen:Empty);}
            }break; default:break;
         }
      }
      return out;
   }
   Position play(const Move&m) const {
      Position p=*this; Cell moving=p.board[m.from]; bool pawn=moving.piece==Pawn; bool capture=p.board[m.to].piece!=Empty;
      if(pawn&&p.board[m.to].piece==Empty&&m.from%10!=m.to%10){int f,r;coordinates(m.to,f,r);p.board[square(f,r+(moving.white?-1:1))]=Cell();capture=true;}
      p.board[m.to]=moving;p.board[m.from]=Cell();if(m.promotion!=Empty)p.board[m.to].piece=m.promotion;
      int ff0,fr0,tf0,tr0;coordinates(m.from,ff0,fr0);coordinates(m.to,tf0,tr0);
      if(moving.piece==King&&std::abs(tf0-ff0)==2){int rook_from=square(tf0>ff0?8:1,fr0),rook_to=square(tf0>ff0?6:4,fr0);p.board[rook_to]=p.board[rook_from];p.board[rook_from]=Cell();}
      if(moving.piece==King){p.castling.erase(std::remove(p.castling.begin(),p.castling.end(),moving.white?'K':'k'),p.castling.end());p.castling.erase(std::remove(p.castling.begin(),p.castling.end(),moving.white?'Q':'q'),p.castling.end());}
      if(m.from==square(1,0)||m.to==square(1,0))p.castling.erase(std::remove(p.castling.begin(),p.castling.end(),'Q'),p.castling.end());
      if(m.from==square(8,0)||m.to==square(8,0))p.castling.erase(std::remove(p.castling.begin(),p.castling.end(),'K'),p.castling.end());
      if(m.from==square(1,9)||m.to==square(1,9))p.castling.erase(std::remove(p.castling.begin(),p.castling.end(),'q'),p.castling.end());
      if(m.from==square(8,9)||m.to==square(8,9))p.castling.erase(std::remove(p.castling.begin(),p.castling.end(),'k'),p.castling.end());
      p.ep.clear();int ff,fr,tf,tr;coordinates(m.from,ff,fr);coordinates(m.to,tf,tr);if(pawn&&std::abs(tr-fr)>1)for(int r=fr+(moving.white?1:-1);r!=tr;r+=(moving.white?1:-1))p.ep.push_back(square(ff,r));
      p.halfmove=(pawn||capture)?0:halfmove+1;if(!white_to_move)p.fullmove++;p.white_to_move=!white_to_move;return p;
   }
   int king(bool white)const{for(int i=0;i<104;i++)if(board[i].piece==King&&board[i].white==white)return i;return -1;}
   bool attacked(int target,bool by_white)const{Position p=*this;p.white_to_move=by_white;std::vector<Move> ms=p.pseudo(false);for(std::size_t i=0;i<ms.size();i++){if(ms[i].to!=target)continue;if(p.board[ms[i].from].piece!=Pawn)return true;int ff,fr,tf,tr;coordinates(ms[i].from,ff,fr);coordinates(target,tf,tr);if(std::abs(tf-ff)==1&&tr-fr==(by_white?1:-1))return true;}return false;}
   bool check(bool white)const{int k=king(white);return k>=0&&attacked(k,!white);}
   std::vector<Move> legal()const{std::vector<Move> out,all=pseudo();for(std::size_t i=0;i<all.size();i++){Position p=play(all[i]);if(!p.check(white_to_move))out.push_back(all[i]);}return out;}
};

static int value(Piece p){static const int v[]={0,100,225,425,600,1200,20000,400,375};return v[int(p)];}
static int evaluate(const Position&p){int score=0;for(int i=0;i<104;i++)if(p.board[i].piece!=Empty)score+=(p.board[i].white?1:-1)*value(p.board[i].piece);return p.white_to_move?score:-score;}
static int search(const Position&p,int depth,int alpha,int beta,std::vector<Move>&pv){
   pv.clear();if(depth<=0)return evaluate(p);std::vector<Move>moves=p.legal();if(moves.empty())return p.check(p.white_to_move)?-30000-depth:0;
   for(std::size_t i=0;i<moves.size();i++){
      std::vector<Move>child;int score=-search(p.play(moves[i]),depth-1,-beta,-alpha,child);
      if(score>alpha){alpha=score;pv.clear();pv.push_back(moves[i]);pv.insert(pv.end(),child.begin(),child.end());}
      if(alpha>=beta)break;
   }
   return alpha;
}
static std::string move_name(const Move&m){std::string s=Position::square_name(m.from)+Position::square_name(m.to);if(m.promotion!=Empty)s+=Position::encode(m.promotion);return s;}
static Move parse_move(const Position&p,const std::string&s){std::vector<Move>ms=p.legal();for(std::size_t i=0;i<ms.size();i++)if(move_name(ms[i])==s)return ms[i];return Move();}

struct Search_Result {
   Move move;
   int score;
   std::vector<Move> pv;
   Search_Result():move(),score(0){}
};

static Search_Result search_root(const Position&p,int depth){
   Search_Result result;std::vector<Move>moves=p.legal();
   if(moves.empty()){result.score=p.check(p.white_to_move)?-30000:0;return result;}

   result.score=std::numeric_limits<int>::min();
   for(std::size_t i=0;i<moves.size();i++){
      std::vector<Move>child;int score=-search(p.play(moves[i]),std::max(0,depth-1),-32000,32000,child);
      if(result.move.from<0||score>result.score){
         result.move=moves[i];result.score=score;result.pv.clear();result.pv.push_back(moves[i]);result.pv.insert(result.pv.end(),child.begin(),child.end());
      }
   }
   return result;
}

} // namespace omega

int main(){
 using namespace omega;Position position;bool position_valid=true;std::string line;
 while(std::getline(std::cin,line)){
  std::stringstream ss(line);std::string cmd;ss>>cmd;
  if(cmd=="uci"){
   std::cout<<"id name Senpai Omega 0.1\nid author Fabien Letouzey and contributors\noption name UCI_Variant type combo default omega var omega\nuciok\n"<<std::flush;
  }else if(cmd=="isready"){
   std::cout<<"readyok\n"<<std::flush;
  }else if(cmd=="ucinewgame"){
   position_valid=position.load(Start);
  }else if(cmd=="position"){
   Position candidate=position;std::string token,fen;bool ok=true,saw_moves=false;
   if(!(ss>>token)){ok=false;
   }else if(token=="startpos"){
    ok=candidate.load(Start);
    if(ss>>token){if(token=="moves")saw_moves=true;else ok=false;}
   }else if(token=="fen"){
    while(ss>>token){if(token=="moves"){saw_moves=true;break;}if(!fen.empty())fen+=' ';fen+=token;}
    ok=!fen.empty()&&candidate.load(fen);
   }else{
    ok=false;
   }

   if(ok&&saw_moves){
    while(ss>>token){Move move=parse_move(candidate,token);if(move.from<0){ok=false;break;}candidate=candidate.play(move);}
   }

   if(ok){position=candidate;position_valid=true;
   }else{position_valid=false;std::cout<<"info string Invalid position\n"<<std::flush;}
  }else if(cmd=="go"){
   if(!position_valid){std::cout<<"info string No valid position\nbestmove 0000\n"<<std::flush;continue;}

   int depth=3;std::string token;
   while(ss>>token)if(token=="depth"){if(!(ss>>depth)||depth<1)depth=1;}

   Search_Result result;
   for(int current_depth=1;current_depth<=depth;current_depth++){
    result=search_root(position,current_depth);
    if(result.move.from<0)break;
    std::cout<<"info depth "<<current_depth<<" score cp "<<result.score<<" pv";
    for(std::size_t i=0;i<result.pv.size();i++)std::cout<<' '<<move_name(result.pv[i]);
    std::cout<<'\n'<<std::flush;
   }

   std::cout<<"bestmove "<<(result.move.from<0?"0000":move_name(result.move));
   if(result.pv.size()>1)std::cout<<" ponder "<<move_name(result.pv[1]);
   std::cout<<'\n'<<std::flush;
  }else if(cmd=="quit"){
   break;
  }
 }
 return 0;
}
