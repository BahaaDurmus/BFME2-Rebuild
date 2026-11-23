// IDirect3DDevice9.h: the Direct3D 9 device as the game calls it through DXWrapper's Direct3D 8 pointer.
// The device behind DXWrapper::D3DDevice is a Direct3D 9 device with Direct3D 8's name (see dxwrapper.h): retail calls
// SetTransform through slot 44, SetRenderState 57, DrawPrimitiveUP 83 and SetFVF 89, which is d3d9.h's order and not d3d8.h's.
// d3d9.h cannot be included next to d3d8.h (both define the same enumerations), so this header (which must not share a unit with d3d9.h either) declares the slots by
// position: the ones the tree calls have their signature, the others are numbered placeholders that only keep the
// table in order. Nothing creates an object of this type; DXWrapper::_Get_D3D_Device9 reinterprets the device pointer,
// so a caller writes device->SetFVF(...) and gets the same indirect call a hand-indexed table call produces.
#pragma once
#ifndef BFME2_IDIRECT3DDEVICE9_H
#define BFME2_IDIRECT3DDEVICE9_H

#include <d3d8.h>

struct IDirect3DVertexDeclaration9;
struct IDirect3DVertexShader9;
struct IDirect3DPixelShader9;

struct IDirect3DDevice9
{
	virtual long __stdcall Slot000() = 0;
	virtual long __stdcall Slot001() = 0;
	virtual long __stdcall Slot002() = 0;
	virtual long __stdcall Slot003() = 0;
	virtual long __stdcall Slot004() = 0;
	virtual long __stdcall Slot005() = 0;
	virtual long __stdcall Slot006() = 0;
	virtual long __stdcall Slot007() = 0;
	virtual long __stdcall Slot008() = 0;
	virtual long __stdcall Slot009() = 0;
	virtual long __stdcall Slot010() = 0;
	virtual long __stdcall Slot011() = 0;
	virtual long __stdcall Slot012() = 0;
	virtual long __stdcall Slot013() = 0;
	virtual long __stdcall Slot014() = 0;
	virtual long __stdcall Slot015() = 0;
	virtual long __stdcall Slot016() = 0;
	virtual long __stdcall Slot017() = 0;
	virtual long __stdcall Slot018() = 0;
	virtual long __stdcall Slot019() = 0;
	virtual long __stdcall Slot020() = 0;
	virtual long __stdcall Slot021() = 0;
	virtual long __stdcall Slot022() = 0;
	virtual long __stdcall Slot023() = 0;
	virtual long __stdcall Slot024() = 0;
	virtual long __stdcall Slot025() = 0;
	virtual long __stdcall Slot026() = 0;
	virtual long __stdcall Slot027() = 0;
	virtual long __stdcall Slot028() = 0;
	virtual long __stdcall Slot029() = 0;
	virtual long __stdcall Slot030() = 0;
	virtual long __stdcall Slot031() = 0;
	virtual long __stdcall Slot032() = 0;
	virtual long __stdcall Slot033() = 0;
	virtual long __stdcall Slot034() = 0;
	virtual long __stdcall Slot035() = 0;
	virtual long __stdcall Slot036() = 0;
	virtual long __stdcall Slot037() = 0;
	virtual long __stdcall Slot038() = 0;
	virtual long __stdcall Slot039() = 0;
	virtual long __stdcall Slot040() = 0;
	virtual long __stdcall Slot041() = 0;
	virtual long __stdcall Slot042() = 0;
	virtual long __stdcall Slot043() = 0;
	virtual long __stdcall SetTransform(D3DTRANSFORMSTATETYPE state, const D3DMATRIX *matrix) = 0;	// slot 44
	virtual long __stdcall Slot045() = 0;
	virtual long __stdcall Slot046() = 0;
	virtual long __stdcall Slot047() = 0;
	virtual long __stdcall Slot048() = 0;
	virtual long __stdcall Slot049() = 0;
	virtual long __stdcall Slot050() = 0;
	virtual long __stdcall Slot051() = 0;
	virtual long __stdcall Slot052() = 0;
	virtual long __stdcall Slot053() = 0;
	virtual long __stdcall Slot054() = 0;
	virtual long __stdcall Slot055() = 0;
	virtual long __stdcall Slot056() = 0;
	virtual long __stdcall SetRenderState(D3DRENDERSTATETYPE state, unsigned long value) = 0;	// slot 57
	virtual long __stdcall Slot058() = 0;
	virtual long __stdcall Slot059() = 0;
	virtual long __stdcall Slot060() = 0;
	virtual long __stdcall Slot061() = 0;
	virtual long __stdcall Slot062() = 0;
	virtual long __stdcall Slot063() = 0;
	virtual long __stdcall Slot064() = 0;
	virtual long __stdcall Slot065() = 0;
	virtual long __stdcall Slot066() = 0;
	virtual long __stdcall Slot067() = 0;
	virtual long __stdcall Slot068() = 0;
	virtual long __stdcall Slot069() = 0;
	virtual long __stdcall Slot070() = 0;
	virtual long __stdcall Slot071() = 0;
	virtual long __stdcall Slot072() = 0;
	virtual long __stdcall Slot073() = 0;
	virtual long __stdcall Slot074() = 0;
	virtual long __stdcall Slot075() = 0;
	virtual long __stdcall Slot076() = 0;
	virtual long __stdcall Slot077() = 0;
	virtual long __stdcall Slot078() = 0;
	virtual long __stdcall Slot079() = 0;
	virtual long __stdcall Slot080() = 0;
	virtual long __stdcall Slot081() = 0;
	virtual long __stdcall Slot082() = 0;
	virtual long __stdcall DrawPrimitiveUP(D3DPRIMITIVETYPE type, unsigned primitive_count, const void *vertices, unsigned stride) = 0;	// slot 83
	virtual long __stdcall Slot084() = 0;
	virtual long __stdcall Slot085() = 0;
	virtual long __stdcall Slot086() = 0;
	virtual long __stdcall SetVertexDeclaration(IDirect3DVertexDeclaration9 *declaration) = 0;	// slot 87
	virtual long __stdcall Slot088() = 0;
	virtual long __stdcall SetFVF(unsigned long fvf) = 0;	// slot 89
	virtual long __stdcall Slot090() = 0;
	virtual long __stdcall Slot091() = 0;
	virtual long __stdcall SetVertexShader(IDirect3DVertexShader9 *shader) = 0;	// slot 92
	virtual long __stdcall Slot093() = 0;
	virtual long __stdcall Slot094() = 0;
	virtual long __stdcall Slot095() = 0;
	virtual long __stdcall Slot096() = 0;
	virtual long __stdcall Slot097() = 0;
	virtual long __stdcall Slot098() = 0;
	virtual long __stdcall Slot099() = 0;
	virtual long __stdcall Slot100() = 0;
	virtual long __stdcall Slot101() = 0;
	virtual long __stdcall Slot102() = 0;
	virtual long __stdcall Slot103() = 0;
	virtual long __stdcall Slot104() = 0;
	virtual long __stdcall Slot105() = 0;
	virtual long __stdcall Slot106() = 0;
	virtual long __stdcall SetPixelShader(IDirect3DPixelShader9 *shader) = 0;	// slot 107
};

#endif
