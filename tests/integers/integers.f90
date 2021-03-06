MODULE integers

  USE ISO_C_BINDING
  IMPLICIT NONE

CONTAINS

  FUNCTION add_ints1(a,b) RESULT(y)
    INTEGER ( KIND = 1 ) , INTENT(in) :: a
    INTEGER(1), INTENT(in) :: b
    INTEGER*1 :: y
    y = a+b
  END FUNCTION add_ints1
  
  FUNCTION add_ints2(a,b) RESULT(y)
    INTEGER*2, INTENT(in) :: a,b
    INTEGER(KIND=2  ) :: y
    y = a+b
  END FUNCTION add_ints2

  FUNCTION add_ints4(a,b) RESULT(y)
    INTEGER (KIND  =4), INTENT(in) :: a
    INTEGER, INTENT(in) :: b
    INTEGER :: y
    y = a+b
  END FUNCTION add_ints4

  FUNCTION add_ints8(a,b) RESULT(y)
    INTEGER (KIND= 8), INTENT(in) :: a
    INTEGER*8, INTENT(in) :: b
    INTEGER(8) :: y
    y = a+b
  END FUNCTION add_ints8

  FUNCTION add_ints_byval(a,b) RESULT(y)
    INTEGER, INTENT(in), VALUE :: a, b
    INTEGER :: y
    y = a + b
  END FUNCTION add_ints_byval
  
  ! Test lower case parsing:

  function add_ints1_lower(a,b) result(y)
    integer ( kind = 1 ) , intent(in) :: a
    integer ( 1), intent(in) :: b
    integer*1 :: y
    y = a+b
  end function add_ints1_lower
  
  function add_ints2_lower(a,b) result(y)
    integer*2, intent(in) :: a,b
    integer(KIND=2  ) :: y
    y = a+b
  end function add_ints2_lower

  function add_ints4_lower(a,b) result(y)
    integer (kind  =4), intent(in) :: a
    integer, intent(in) :: b
    integer :: y
    y = a+b
  end function add_ints4_lower

  FUNCTION add_iso_ints(a,b) RESULT(y)
    INTEGER (C_INT) , INTENT(in) :: a, b
    INTEGER (C_INT) :: y
    y = a+b
  END FUNCTION add_iso_ints

  FUNCTION add_iso_longs(a,b) RESULT(y)
    INTEGER (C_LONG) , INTENT(in) :: a, b
    INTEGER (C_LONG) :: y
    y = a+b
  END FUNCTION add_iso_longs
  
END MODULE integers
