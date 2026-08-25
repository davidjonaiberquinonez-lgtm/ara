<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
	<head>
		    <meta charset="utf-8">
	<meta http-equiv="X-UA-Compatible" content="IE=edge">
	<meta name="viewport" content="width=device-width, initial-scale=1">
	
	<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.3.0/font/bootstrap-icons.css">
	<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet" integrity="sha384-QWTKZyjpPEjISv5WaRU9OFeRpok6YctnYmDr5pNlyT2bRjXh0JMhjY6hW+ALEwIH" crossorigin="anonymous">
	
	<link rel="stylesheet" href="css/style.css">
	<link rel="stylesheet" href="css/custom.css">
	<link rel="icon" href='img/logo-icon.png' size="32x32" type="image/png">	</head>
		<body>
		<div class="container">
			<div class="row">
				<div class="col-12 row"><!-----------botones logo y buscador------------->
					<div class="col-sm-2 logo text-center row">
						<div class="d-sm-none col-sm-12">
							<a href="index.php"><img src="img/logo.png"></a>
						</div>
						<div class="col-sm-12 col-6">
							<a href="listado.php" class="btn btn-lg text-light" style="background-color: #1A9888; font-size: 14px;">Lista de Despachos</a>
						</div>
						<div class="col-sm-12 col-6">
							<a href="cerrar.php" class="btn btn-danger" style="font-size: 14px;">Salir</a>
						</div>
					</div>
					<div class="col-sm-9 col-12 row">
						<form action="lista.php" method="POST" >
							<div class="row">
																	
								<div class="col-4 text-center">
									<h3><b>GUIA:</b> 23008</h3>
								</div>
								<div class="col-4 text-center">
									<h3><b>SEG:</b> (FORANEO) FRONTERA                                          </h3>
								</div>
								<div class="col-4 text-center">
									<h3><b>RESP:</b> 43</h3>
								</div>
								<div class="col-12 text-center">
																					<div class="form-group row text-end">
													<label for="nota" class="col-2 col-form-label col-form-label-lg">Nº Nota: </label>
													<div class="col-8">
														<input type="text" name="nota" class="form-control" style="font-size: 18px;" autocomplete="OFF" required autofocus />
													</div>
													<button class="btn btn-success col-2" name="consulta" style="font-size:16px;">Buscar</button>
												</div>
																			</div>
							</div>
						</form>
					</div>
					<div class="d-none d-sm-block col-1 logo row">
						<div class="col-12">
							<a href="index.php"><img src="img/logo.png"></a>
						</div>
					</div>
				</div>
								
				<div class="col-sm-6 row align-items-start"><!-----------Notas cargadas------------->
										<div class="col-sm-12" style="">
												
						<table class="table" border="1">
							<tr class="">
								<th colspan="3" class="">Total notas: 0</th>
								<th colspan="2" class="text-end">Total Peso: 0,0000</th>
							</tr>
							<tr class="">
								<th class="text-center text-light bg-success">Nº</th>
								<th class="text-center text-light bg-success">Nº NOTA</th>
								<th class="text-center text-light bg-success">PAQUETES</th>
								<th class="text-center text-light bg-success">PESO</th>
								<th class="text-center bg-success"><a href="registro.php" class="btn btn-light" name="registro">Finalizar</a></th>
							</tr>
														<tr>
								<td colspan="5" class="text-center">
									<a href="registro.php" class="btn btn-success" name="registro">Finalizar</a>
								</td>
							</tr>
						</table>
					</div>
				</div>
				
				
				<div class="col-6 row align-items-start"><!-----------notas por cargar------------->
					<table class="table" style="heigth: auto;">
						<tr class="">
							<th height="20px" class="bg-success text-light text-center">Nota</th>
							<th height="20px" class="bg-success text-light text-center">Codigo</th>
							<th height="20px" class="bg-success text-light text-center">Descripcion</th>
							<th height="20px" class="bg-success text-light text-center">Creada</th>
							<th height="20px" class="bg-success text-light text-center">Impresa</th>
							<th height="20px" class="bg-success text-light text-center">Estado</th>
						</tr>
															<tr class="">
										<td style='background-color: #fff;'>72162768</td>
										<td style='background-color: #fff;'>FAR01943  </td>
										<td style='background-color: #fff;'>ATENEA FARMA VIDA Y SALUD, C.A                                                                      </td>
										<td style='background-color: #fff;'>11/08/2026 09:53:00</td>
										<td style='background-color: #fff;'><sapn class=''>SI</span></td>
										<td style='background-color: #fff;'><sapn class=''>Procesada</span></td>
									</tr>
																	<tr class="">
										<td style='background-color: #fff;'>72163087</td>
										<td style='background-color: #fff;'>FAR03462  </td>
										<td style='background-color: #fff;'>MERKGUSTO FARMACIA, C.A                                                                             </td>
										<td style='background-color: #fff;'>11/08/2026 15:06:00</td>
										<td style='background-color: #fff;'><sapn class=''>SI</span></td>
										<td style='background-color: #fff;'><sapn class=''>Procesada</span></td>
									</tr>
																	<tr class="">
										<td style='background-color: #fff;'>72163322</td>
										<td style='background-color: #fff;'>FAR01943  </td>
										<td style='background-color: #fff;'>ATENEA FARMA VIDA Y SALUD, C.A                                                                      </td>
										<td style='background-color: #fff;'>11/08/2026 18:18:00</td>
										<td style='background-color: #fff;'><sapn class=''>SI</span></td>
										<td style='background-color: #fff;'><sapn class=''>Procesada</span></td>
									</tr>
																	<tr class="">
										<td style='background-color: #fff;'>471093</td>
										<td style='background-color: #fff;'>FAR00793  </td>
										<td style='background-color: #fff;'>FARMAGAMA 24 C.A                                                                                    </td>
										<td style='background-color: #fff;'>12/08/2026 11:54:00</td>
										<td style='background-color: #fff;'><sapn class=''>SI</span></td>
										<td style='background-color: #fff;'><sapn class='text-danger'>S/P</span></td>
									</tr>
													</table>
				</div>
			
				
			</div>
		</div>
	</body>
</html>