/* Parses a JSON document with libjq (an OpenBSD package dependency). */
#include <stdio.h>
#include <jv.h>

int
main(void)
{
	jv v = jv_parse("{\"os\":\"OpenBSD\",\"cross\":true}");
	jv os = jv_object_get(jv_copy(v), jv_string("os"));

	printf("libjq parsed os=%s\n", jv_string_value(os));
	jv_free(os);
	jv_free(v);
	return 0;
}
