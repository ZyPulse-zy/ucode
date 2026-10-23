struct pair { int x; int y; };
int add(int a, int b) { return a + b; }
int sum_array(int *p, int n) { int s=0; for(int i=0;i<n;i++) s+=p[i]; return s; }
int sum_pair(struct pair p) { return p.x + p.y; }
int invoke(int (*fn)(int), int n) { return fn(n); }
double complex_real(double _Complex z) { return __real__ z; }
double _Complex complex_result(void) { return 1.0 + 2.0i; }
double complex_ptr_real(const double _Complex *z) { return __real__ *z; }
double complex_ptr_imag(const double _Complex *z) { return __imag__ *z; }
