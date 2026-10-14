#include <cassert>
#include <cstdio>
#include "a08/selftest.hpp"
int main(){
 a08::Pipeline p;
 assert(a08::selftest(p)==0);
 const a07::Calibration cal={{500,500,500,500},{3000,3000,3000},4095,10000,10000,3950,0,true};
 assert(p.reset(cal,7,true));
 a07::Sample s{};s.boot=42;s.lid=0;
 for(int j=0;j<5;j++)s.adc[j]=2048;
 for(unsigned i=0;i<1300;i++){
   s.ms=i*50;s.sequence=i;s.lid=i>=100&&i<120?1:0;
   if(p.push(s)){
     assert(p.frame[6]==0); // physical live origin
     assert(p.frame[34]==0&&p.frame[41]==0); // unknown phase/quality
     assert(p.frame[42]==0&&p.frame[43]==255&&p.frame[44]==255&&p.frame[45]==0);
     if(i==101)assert(p.frame[27]==1&&p.frame[35]==1);
     if(i==121)assert(p.frame[27]==0&&p.frame[35]==1);
   }
 }
 // Timing faults still invalidate contact; never smooth gaps into good coverage.
 s.ms+=500;s.sequence++;
 assert(p.push(s)&&p.frame[27]==255&&p.frame[35]==0);
 // A reset defaults back to test; synthetic self-tests cannot inherit live mode.
 assert(p.reset(cal));s.ms=0;s.sequence=0;
 assert(p.push(s)&&p.frame[6]==1);
 puts("A08 selftest and live observation boundary PASS");
}
