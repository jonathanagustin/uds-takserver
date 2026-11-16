#include "core/AbstractLayer.h"
#include "interop/Pointer.h"
#include "common.h"
class Sentinel final:public atakmap::core::AbstractLayer {public:Sentinel():AbstractLayer("empty-pointer-sentinel") {}};
extern "C" JNIEXPORT jlongArray JNICALL Java_com_atakmap_map_layer_PointerEmptyControls_check(JNIEnv *env,jclass){
 std::shared_ptr<atakmap::core::Layer> empty,value(new Sentinel());
 jobject pointer=TAKEngineJNI::Interop::NewPointer<atakmap::core::Layer>(env,empty);
 if(env->ExceptionCheck()||!pointer)return nullptr;
 auto status=TAKEngineJNI::Interop::Pointer_get<atakmap::core::Layer>(value,*env,pointer);
 bool shared=TAKEngineJNI::Interop::Pointer_isShared<atakmap::core::Layer>(env,pointer);
 bool promoted=TAKEngineJNI::Interop::Pointer_makeShared<atakmap::core::Layer>(env,pointer);
 if(env->ExceptionCheck())return nullptr;
 jlong result[]={status,shared?1:0,promoted?1:0,value?0:1};
 TAKEngineJNI::Interop::Pointer_destruct<atakmap::core::Layer>(env,pointer);
 env->DeleteLocalRef(pointer);if(env->ExceptionCheck())return nullptr;
 jlongArray output=env->NewLongArray(4);if(output)env->SetLongArrayRegion(output,0,4,result);return output;
}
