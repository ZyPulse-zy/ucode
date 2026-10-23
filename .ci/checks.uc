import * as ffi from 'ffi';
let passed = [];
function check(name, value) {
    if (!value) die(name + " failed");
    push(passed, name);
}
function rejected(name, fn) {
    let seen = false;
    try { fn(); }
    catch (e) { seen = e.type == 'Type error'; }
    check(name, seen);
}
let lib = ffi.dlopen(ARGV[0], false, `
    struct pair { int x; int y; };
    int add(int, int);
    int sum_array(int *, int);
    int sum_pair(struct pair);
    int invoke(int (*)(int), int);
    double complex_real(double complex);
    double complex complex_result(void);
    double complex_ptr_real(const double complex *);
    double complex_ptr_imag(const double complex *);
`);
check('scalar', lib.add(19, 23) == 42);
let array = ffi.ctype('int[3]', [10, 12, 20]);
check('array', lib.sum_array(array.ptr(), 3) == 42);
check('struct-by-value', lib.sum_pair({x: 19, y: 23}) == 42);
let callback = ffi.closure('int (*)(int)', x => x + 1);
check('closure-call', lib.invoke(callback, 41) == 42);
for (let ct in ['float complex', 'double complex', 'long double complex']) {
    let z = ffi.ctype(ct, 1.5, 2.5);
    check(ct + '-store-repr', index(z.tostring(), '1.5+2.5i') >= 0);
}
let z = ffi.ctype('double complex', 1.5, -2.5);
check('complex-pointer-real', lib.complex_ptr_real(z.ptr()) == 1.5);
check('complex-pointer-imag', lib.complex_ptr_imag(z.ptr()) == -2.5);
check('descriptor-unavailable', ffi.C.dlsym('ffi_type_complex_double') == null);
rejected('complex-argument-rejected', () => lib.complex_real([1.5, -2.5]));
rejected('complex-return-rejected', () => lib.complex_result());
rejected('complex-closure-argument-rejected', () => ffi.closure('double (*)(double complex)', x => 1));
rejected('complex-closure-return-rejected', () => ffi.closure('double complex (*)(void)', () => [1.5, -2.5]));
check('scalar-after-rejection', lib.add(19, 23) == 42);
print(sprintf('%J\n', passed));
